"""Live Dhan market-data provider (market data only — execution lives in app/execution/dhan_live.py).

* Instrument master: downloaded, versioned (sha256) and stored; lot sizes, security ids, expiries and the
  index/futures identifiers are read from it (never hard-coded).
* REST: option chain every `chain_poll_seconds` (>= 3 s), 1-minute intraday candles for index spot and
  current-month futures every 15 s (resampled to 5m/15m with session anchoring). Candles whose period
  has not ended are marked incomplete.
* WebSocket v2: index ticker packets (LTP for triggers/exits), futures quote packets, and full packets
  (best bid/ask) for selected/held option contracts. Reconnects with exponential backoff; while
  disconnected, a feed fault blocks new entries.
* Faults (token invalid, rate limit, disconnect, stale data, parse errors) are surfaced in `status()`
  and passed to the freshness gate. Cached data is never presented as fresh: every item keeps its
  own received time.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import threading
import time as _time
from datetime import datetime, date, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select

from app import db as dbm
from app.config import settings
from app.core.types import IST, Index, Tick, OptionQuote, OptionChainSnapshot, Candle
from app.data.dhan.client import DhanClient, DhanError
from app.data.dhan.parsers import (parse_option_chain, parse_expiry_list, parse_candles, parse_ws_packet,
                                   parse_instrument_master, ParseError)
from app.data.provider import MarketDataProvider, FeedStatus, InstrumentInfo
from app.strategy.candles import resample
from app.strategy.engine import merge_futures_volume
from app.strategy.levels import previous_day_from_intraday

log = logging.getLogger("dhan.feed")
logging.getLogger("websockets").setLevel(logging.WARNING)     # never log the tokenised URL

WS_STALE_S = 15.0
CANDLE_POLL_S = 15.0
HISTORY_DAYS = 7


def load_instrument_master(Session, url: str, http: Optional[httpx.Client] = None) -> int:
    """Download, parse, version and store the instrument master. Returns version id."""
    http = http or httpx.Client(timeout=60)
    r = http.get(url)
    r.raise_for_status()
    rows, meta = parse_instrument_master(r.content)
    with Session() as s:
        existing = s.execute(select(dbm.InstrumentMasterVersion).where(dbm.InstrumentMasterVersion.sha256 == meta["sha256"])).scalar_one_or_none()
        if existing:
            return existing.id
        v = dbm.InstrumentMasterVersion(fetched_at=datetime.now(IST), source_url=url, sha256=meta["sha256"],
                                        row_count=meta["row_count"], columns={"columns": meta["columns"]})
        s.add(v)
        s.flush()
        for r_ in rows:
            s.add(dbm.Instrument(master_version_id=v.id, **r_))
        s.commit()
        return v.id


class DhanMarketData(MarketDataProvider):
    source = "dhan"

    def __init__(self, client: DhanClient, Session, access_token: str, client_id: str,
                 chain_poll_s: float = 5.0):
        self.client = client
        self.Session = Session
        self._tok = access_token
        self._cid = client_id
        self.chain_poll_s = max(3.0, chain_poll_s)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._status = FeedStatus(connected=False, source="dhan", token_status="unknown")
        self._c1: dict[str, list[Candle]] = {}
        self._f1: dict[str, list[Candle]] = {}
        self._chains: dict[str, list[OptionChainSnapshot]] = {}
        self._ticks: dict[str, Tick] = {}
        self._ws_quotes: dict[str, tuple[dict, datetime]] = {}
        self._fut_quote: dict[str, dict] = {}
        self._instr: dict[str, InstrumentInfo] = {}
        self._ids: dict[str, dict] = {}
        self._expiry: dict[str, date] = {}
        self._subs: set[tuple[str, str, int]] = set()
        self._pending_subs: list[tuple[str, str, int]] = []
        self._ws_last_msg: Optional[datetime] = None
        self._threads: list[threading.Thread] = []
        self.master_version: Optional[int] = None

    # ---------------- setup ----------------
    def resolve_instruments(self):
        with self.Session() as s:
            v = s.execute(select(dbm.InstrumentMasterVersion).order_by(dbm.InstrumentMasterVersion.id.desc())).scalars().first()
            if v is None:
                raise RuntimeError("Instrument master not loaded")
            self.master_version = v.id
            rows = s.execute(select(dbm.Instrument).where(dbm.Instrument.master_version_id == v.id)).scalars().all()
        today = datetime.now(IST).date()
        for idx in ("NIFTY", "BANKNIFTY"):
            spot = [r for r in rows if r.instrument == "INDEX" and r.trading_symbol == idx]
            futs = sorted([r for r in rows if r.instrument == "FUTIDX" and r.underlying == idx and r.expiry and r.expiry >= today],
                          key=lambda r: r.expiry)
            if not spot:
                raise RuntimeError(f"{idx}: index row not found in instrument master")
            self._ids[idx] = {"spot": spot[0].security_id, "fut": futs[0].security_id if futs else None,
                              "fut_expiry": futs[0].expiry if futs else None}
        for r in rows:
            if r.instrument == "OPTIDX":
                self._instr[r.security_id] = InstrumentInfo(r.security_id, r.exchange_segment, r.trading_symbol, r.expiry,
                                                            r.strike, r.option_type, r.lot_size, r.tick_size, v.id)

    def refresh_expiries(self):
        today = datetime.now(IST).date()
        for idx in ("NIFTY", "BANKNIFTY"):
            exps = parse_expiry_list(self.client.expiry_list(int(self._ids[idx]["spot"]), "IDX_I"))
            act = [e for e in exps if e >= today]
            if not act:
                raise RuntimeError(f"{idx}: no active expiry")
            self._expiry[idx] = act[0]

    # ---------------- provider interface ----------------
    def candles(self, index: str, tf_minutes: int) -> list[Candle]:
        with self._lock:
            c1 = list(self._c1.get(index, []))
            f1 = list(self._f1.get(index, []))
        now = datetime.now(IST)
        if tf_minutes == 1:
            return merge_futures_volume(c1, f1)
        spot = resample(c1, tf_minutes, now=now)
        fut = resample(f1, tf_minutes, now=now)
        return merge_futures_volume(spot, fut)

    def chain(self, index):
        with self._lock:
            lst = self._chains.get(index, [])
            cur = lst[-1] if lst else None
            prev = lst[-2] if len(lst) >= 2 and lst[-2].expiry == (cur.expiry if cur else None) else None
        return cur, prev

    def tick(self, index):
        with self._lock:
            return self._ticks.get(index)

    def option_quote(self, security_id):
        with self._lock:
            base = None
            for idx, lst in self._chains.items():
                if lst:
                    for r in lst[-1].rows:
                        if r.security_id == security_id:
                            base = (r, lst[-1].received_at)
                            break
            ws = self._ws_quotes.get(security_id)
        if ws and (base is None or ws[1] > base[1]):
            q = copy.copy(base[0]) if base else None
            if q is None:
                info = self._instr.get(security_id)
                if not info:
                    return None
                from app.core.types import OptionType
                q = OptionQuote(info.strike, OptionType(info.option_type), security_id=security_id)
            d = ws[0]
            q.ltp = d.get("ltp") or q.ltp
            top = (d.get("depth") or [{}])[0]
            if top.get("bid"):
                q.bid, q.bid_qty = top["bid"], top.get("bid_qty")
            if top.get("ask"):
                q.ask, q.ask_qty = top["ask"], top.get("ask_qty")
            return q, ws[1]
        return base

    def instrument(self, security_id):
        return self._instr.get(security_id)

    def daily_prev(self, index, today):
        with self._lock:
            c1 = list(self._c1.get(index, []))
        return previous_day_from_intraday(c1, today)

    def status(self):
        st = self._status
        faults = []
        now = datetime.now(IST)
        if self.client.token_invalid:
            faults.append("Dhan access token invalid or expired")
            st.token_status = "invalid"
        elif self.client.last_ok_at:
            st.token_status = "ok"
        if self.client.rate_limited_until and datetime.now(timezone.utc) < self.client.rate_limited_until:
            faults.append("Dhan rate limit active")
        st.rate_limited_until = self.client.rate_limited_until
        if not st.connected:
            faults.append("Market feed WebSocket disconnected")
        elif self._ws_last_msg and (now - self._ws_last_msg).total_seconds() > WS_STALE_S and self._in_session(now):
            faults.append(f"No WebSocket data for {(now - self._ws_last_msg).total_seconds():.0f}s")
        st.faults = faults
        return st

    def watch_option(self, security_id: str):
        """Subscribe full packets (best bid/ask) for a contract being traded or considered."""
        key = ("NSE_FNO", security_id, 21)
        if key not in self._subs:
            self._subs.add(key)
            self._pending_subs.append(key)

    @staticmethod
    def _in_session(now: datetime) -> bool:
        return now.weekday() < 5 and datetime.strptime("09:15", "%H:%M").time() <= now.time() <= datetime.strptime("15:30", "%H:%M").time()

    def _error(self, src: str, msg: str):
        self._status.errors.append({"at": datetime.now(IST).isoformat(), "source": src, "message": msg})
        self._status.errors = self._status.errors[-50:]
        log.warning("%s: %s", src, msg)

    # ---------------- REST loops ----------------
    def _poll_chain(self, idx: str):
        exp = self._expiry.get(idx)
        if exp is None:
            return
        rx = datetime.now(IST)
        raw = self.client.option_chain(int(self._ids[idx]["spot"]), "IDX_I", exp.isoformat())
        snap = parse_option_chain(raw, Index(idx), exp, rx)
        import hashlib
        payload = json.dumps(raw, sort_keys=True)
        with self.Session() as s:
            s.add(dbm.ChainSnapshotRow(index=idx, expiry=exp, underlying=snap.underlying_ltp, feed_ts=None, received_at=rx,
                                       source=snap.source, sha256=hashlib.sha256(payload.encode()).hexdigest(),
                                       raw=payload, normalized=snap.to_dict()))
            s.commit()
        with self._lock:
            lst = self._chains.setdefault(idx, [])
            lst.append(snap)
            del lst[:-20]
            self._status.last_chain_at[idx] = rx

    def _poll_candles(self, idx: str, initial: bool):
        now = datetime.now(IST)
        frm = (now - timedelta(days=HISTORY_DAYS if initial else 1)).date().isoformat()
        to = (now + timedelta(days=1)).date().isoformat()
        sp = parse_candles(self.client.intraday(self._ids[idx]["spot"], "IDX_I", "INDEX", 1, frm, to), "dhan:intraday:spot", 1, now)
        fut = []
        if self._ids[idx]["fut"]:
            fut = parse_candles(self.client.intraday(self._ids[idx]["fut"], "NSE_FNO", "FUTIDX", 1, frm, to, oi=True),
                                "dhan:intraday:fut", 1, now)
        with self._lock:
            self._c1[idx] = self._merge(self._c1.get(idx, []), sp)
            self._f1[idx] = self._merge(self._f1.get(idx, []), fut)
            if sp:
                self._status.last_candle_at[idx] = now

    @staticmethod
    def _merge(old: list[Candle], new: list[Candle]) -> list[Candle]:
        if not new:
            return old
        cut = new[0].ts
        return [c for c in old if c.ts < cut] + new

    def _rest_loop(self):
        initial = True
        last_chain = {i: 0.0 for i in ("NIFTY", "BANKNIFTY")}
        last_candle = 0.0
        while not self._stop.is_set():
            t = _time.monotonic()
            try:
                if initial or t - last_candle >= CANDLE_POLL_S:
                    for idx in ("NIFTY", "BANKNIFTY"):
                        self._poll_candles(idx, initial)
                    last_candle = t
                    initial = False
                for idx in ("NIFTY", "BANKNIFTY"):
                    if t - last_chain[idx] >= self.chain_poll_s:
                        self._poll_chain(idx)
                        last_chain[idx] = _time.monotonic()
            except DhanError as e:
                self._error("rest", str(e))
                if e.kind in ("AUTH",):
                    self._stop.wait(30)
                elif e.kind == "RATE_LIMIT":
                    self._stop.wait(60)          # do not hammer when rate limited
            except ParseError as e:
                self._error("parse", str(e))
            except Exception as e:  # pragma: no cover
                self._error("rest", f"{type(e).__name__}: {e}")
            self._stop.wait(1.0)

    # ---------------- WebSocket ----------------
    def _on_packet(self, p: dict):
        now = datetime.now(IST)
        self._ws_last_msg = now
        sid = p.get("security_id")
        if p["type"] == "disconnect":
            self._error("ws", f"server disconnect code {p.get('reason_code')}")
            return
        with self._lock:
            for idx, ids in self._ids.items():
                if p["type"] == "ticker" and sid == ids["spot"]:
                    self._ticks[idx] = Tick(idx, p["ltp"], p["ltt"].astimezone(IST), now, "dhan:ws:ticker")
                    self._status.last_tick_at[idx] = now
                if p["type"] in ("quote", "full") and sid == ids.get("fut"):
                    self._fut_quote[idx] = {**p, "received_at": now}
            if p["type"] == "full" and sid in self._instr:
                self._ws_quotes[sid] = (p, now)

    async def _ws_main(self):
        import websockets
        backoff = 1.0
        while not self._stop.is_set():
            url = f"{settings.dhan_feed_url}?version=2&token={self._tok}&clientId={self._cid}&authType=2"
            try:
                async with websockets.connect(url, ping_interval=10, ping_timeout=10, max_size=2 ** 20) as ws:
                    self._status.connected = True
                    backoff = 1.0
                    subs = [("IDX_I", self._ids[i]["spot"], 15) for i in self._ids]
                    subs += [("NSE_FNO", self._ids[i]["fut"], 17) for i in self._ids if self._ids[i]["fut"]]
                    subs += list(self._subs)
                    await self._send_subs(ws, subs)
                    while not self._stop.is_set():
                        if self._pending_subs:
                            new, self._pending_subs = self._pending_subs, []
                            await self._send_subs(ws, new)
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=5)
                        except asyncio.TimeoutError:
                            continue
                        if isinstance(msg, bytes):
                            try:
                                p = parse_ws_packet(msg)
                                if p:
                                    self._on_packet(p)
                            except ParseError as e:
                                self._error("ws-parse", str(e))
            except Exception as e:
                self._error("ws", f"{type(e).__name__}")   # message may contain URL => only type
            self._status.connected = False
            await asyncio.sleep(min(backoff, 60))
            backoff *= 2

    @staticmethod
    async def _send_subs(ws, subs):
        by_code: dict[int, list] = {}
        for seg, sid, code in subs:
            by_code.setdefault(code, []).append({"ExchangeSegment": seg, "SecurityId": str(sid)})
        for code, lst in by_code.items():
            for i in range(0, len(lst), 100):
                batch = lst[i:i + 100]
                await ws.send(json.dumps({"RequestCode": code, "InstrumentCount": len(batch), "InstrumentList": batch}))

    def futures_oi_change(self, index):
        """(price change, OI change) of current-month futures: latest completed 1m candle today vs the
        previous session's last candle. (None, None) when either side is unavailable."""
        with self._lock:
            f1 = [c for c in self._f1.get(index, []) if c.complete and c.oi is not None]
        if not f1:
            return None, None
        today = datetime.now(IST).date()
        cur = [c for c in f1 if c.ts.date() == today]
        prev = [c for c in f1 if c.ts.date() < today]
        if not cur or not prev:
            return None, None
        return cur[-1].close - prev[-1].close, cur[-1].oi - prev[-1].oi

    # ---------------- lifecycle ----------------
    def start(self):
        self.resolve_instruments()
        self.refresh_expiries()
        t1 = threading.Thread(target=self._rest_loop, name="dhan-rest", daemon=True)
        t2 = threading.Thread(target=lambda: asyncio.run(self._ws_main()), name="dhan-ws", daemon=True)
        self._threads = [t1, t2]
        t1.start()
        t2.start()

    def stop(self):
        self._stop.set()
