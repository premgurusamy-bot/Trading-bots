"""TradingService — orchestrates data -> engine -> signals -> risk -> broker -> exits -> persistence.

Safety properties enforced here (each is covered by tests):
* Paper mode is the default; live requires the feature flag, explicit UI enablement with risk
  acknowledgement, and per-order confirmation of a final pre-trade summary.
* Every live/paper entry re-validates fresh data, contract identity, funds, position state, trade
  count, daily loss, per-trade risk, signal validity and order size immediately before sending.
* Idempotency: signal ids, client order ids (trade_id-ENTRY / -EXIT-n) and fill ids are unique;
  replays never create duplicate signals, orders or fills.
* Positions/exits are updated only from broker-reported fills. Unknown order state blocks entries.
* All state (signals, trades, orders, fills, daily counters, secure-exit floors, paper cash) is
  persisted on every change and restored by `load_state()` after a restart.
"""
from __future__ import annotations

import logging
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, date, timedelta
from typing import Callable, Optional

from sqlalchemy import select

from app import db as dbm
from app.core.types import IST, Tick
from app.data.provider import MarketDataProvider
from app.execution.broker_base import Broker, BrokerOrder, OrderRequest, OrderStatus, FINAL, Fill
from app.execution.paper import PaperBroker
from app.risk import secure_exit as sx
from app.risk.charges import ChargeSchedule, leg_charges, round_trip_estimate
from app.risk.exits import ExitPlanState, on_price as exit_on_price, on_candle_close as exit_on_close
from app.risk.limits import DailyState, GateFlags, risk_gate, record_entry, record_exit
from app.risk.sizing import size_position
from app.strategy import indicators as ind
from app.strategy.engine import MarketContext, evaluate, still_valid, snapshot_hash, HISTORY_BARS_5M
from app.strategy.freshness import check_quality
from app.strategy.params import StrategyParams, STRATEGY_VERSION
from app.strategy.setups import premium_plan
from app.strategy.signals import Signal, SignalBook, SignalState
from app.strategy.structure import confirmed_pivots, structure_state

log = logging.getLogger("service")
INDICES = ("NIFTY", "BANKNIFTY")
ENTRY_FILL_TIMEOUT_S = 10.0
MAX_EXIT_RETRIES = 3


@dataclass
class TradeRuntime:
    id: str
    signal_id: str
    index: str
    mode: str
    direction: str
    setup: str
    option: dict
    lot_size: int
    sizing: dict
    exit_state: ExitPlanState
    secure: sx.SecureExitState
    state: str = "PENDING_ENTRY"
    entry_order: Optional[BrokerOrder] = None
    exit_orders: list = field(default_factory=list)
    entry_avg: Optional[float] = None
    filled_qty: int = 0
    realised_gross: float = 0.0
    exit_value: float = 0.0
    exit_qty: int = 0
    exit_order_count: int = 0
    exit_retries: int = 0
    premium_high: Optional[float] = None
    premium_low: Optional[float] = None
    u_high: Optional[float] = None
    u_low: Optional[float] = None
    exit_reason: Optional[str] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    snapshot_ref: Optional[str] = None
    expected_entry: Optional[float] = None
    events: list = field(default_factory=list)
    awaiting_confirmation: bool = False
    processed_fills: set = field(default_factory=set)


class TradingService:
    def __init__(self, provider: MarketDataProvider, session_factory, params: StrategyParams | None = None,
                 secure_cfg: sx.SecureExitConfig | None = None, schedule: ChargeSchedule | None = None,
                 paper_capital: float = 100000.0, clock: Callable[[], datetime] | None = None,
                 require_tick: bool = True):
        self.provider = provider
        self.Session = session_factory
        self.p = params or StrategyParams()
        self.secure_cfg = secure_cfg or sx.SecureExitConfig()
        self.schedule = schedule or ChargeSchedule()
        self.clock = clock or (lambda: datetime.now(IST))
        self.require_tick = require_tick
        self.mode = "PAPER"                         # default, always
        self.live_enabled = False
        self.paper_capital = paper_capital
        self.broker: Broker = PaperBroker(self._quote_for_broker, paper_capital, tick=self.p.tick_size,
                                          slippage_ticks=self.p.slippage_ticks)
        self.live_broker: Optional[Broker] = None
        self.book = SignalBook()
        self.flags = GateFlags()
        self.daily = DailyState(self.clock().date())
        self.trades: dict[str, TradeRuntime] = {}
        self.last_candle: dict[str, Optional[datetime]] = {i: None for i in INDICES}
        self.last_eval: dict[str, dict] = {}
        self.events: deque = deque(maxlen=500)
        self.research_watch: dict[str, dict] = {}
        self.listeners: list[Callable[[dict], None]] = []
        self.entries_enabled = True

    # ------------------------------------------------------------------ helpers
    def _quote_for_broker(self, security_id):
        return self.provider.option_quote(security_id)

    def emit(self, kind: str, data: dict):
        ev = {"kind": kind, "at": self.clock().isoformat(), **data}
        self.events.append(ev)
        for fn in list(self.listeners):
            try:
                fn(ev)
            except Exception:  # pragma: no cover
                log.exception("listener failed")

    def sys_event(self, level: str, source: str, message: str, detail: dict | None = None):
        with self.Session() as s:
            s.add(dbm.SystemEvent(ts=self.clock(), level=level, source=source, message=message, detail=detail))
            s.commit()
        self.emit("system", {"level": level, "source": source, "message": message})

    def audit(self, actor: str, action: str, detail: dict):
        with self.Session() as s:
            s.add(dbm.AuditLog(ts=self.clock(), actor=actor, action=action, detail=detail))
            s.commit()

    # ------------------------------------------------------------------ persistence
    def _save_daily(self):
        with self.Session() as s:
            row = s.get(dbm.DailyStateRow, (self.daily.day, self.mode))
            if row is None:
                row = dbm.DailyStateRow(day=self.daily.day, mode=self.mode, state={})
                s.add(row)
            row.state = self.daily.to_dict()
            row.updated_at = self.clock()
            s.commit()

    def _save_signal(self, sig: Signal):
        with self.Session() as s:
            row = s.get(dbm.SignalRow, sig.id)
            if row is None:
                row = dbm.SignalRow(id=sig.id, index=sig.index, direction=sig.direction, setup=sig.setup,
                                    state=sig.state.value, score=sig.score["total"], day=sig.confirm_ts.date(),
                                    data=sig.to_dict(), created_at=sig.created_at)
                s.add(row)
            row.state = sig.state.value
            row.data = sig.to_dict()
            row.updated_at = self.clock()
            for h in sig.history:
                exists = s.execute(select(dbm.SignalEvent).where(dbm.SignalEvent.signal_id == sig.id,
                                                                 dbm.SignalEvent.to_state == h["to"])).first()
                if not exists:
                    s.add(dbm.SignalEvent(signal_id=sig.id, from_state=h["from"], to_state=h["to"],
                                          at=datetime.fromisoformat(h["at"]), reason=h["reason"]))
            s.commit()

    def _save_snapshot(self, res, ref: str):
        with self.Session() as s:
            if s.get(dbm.DecisionSnapshot, ref) is None:
                s.add(dbm.DecisionSnapshot(ref=ref, index=res.index, candle_ts=res.candle_ts,
                                           strategy_version=STRATEGY_VERSION, params_hash=res.snapshot["params_hash"],
                                           snapshot=res.snapshot, levels=res.levels, evaluation=res.to_dict()))
                s.commit()

    def _save_research(self, rid: str, index: str, kind: str, score_total: int, reasons: list, data: dict,
                       snapshot_ref: Optional[str], day: date):
        with self.Session() as s:
            if s.get(dbm.ResearchLog, rid) is None:
                s.add(dbm.ResearchLog(id=rid, index=index, day=day, kind=kind, score=score_total, reasons=reasons,
                                      data=data, snapshot_ref=snapshot_ref))
                s.commit()
        sel = (data.get("selection") or {}).get("selected") if isinstance(data, dict) else None
        if sel and sel.get("quote", {}).get("security_id"):
            self.research_watch[rid] = {"security_id": sel["quote"]["security_id"], "hi": None, "lo": None}

    def _save_trade(self, t: TradeRuntime):
        with self.Session() as s:
            row = s.get(dbm.TradeRow, t.id)
            if row is None:
                row = dbm.TradeRow(id=t.id, signal_id=t.signal_id, index=t.index, mode=t.mode, day=t.opened_at.date(),
                                   direction=t.direction, setup=t.setup, state=t.state, option=t.option,
                                   lots=t.sizing.get("lots", 0), qty=t.sizing.get("qty", 0), open_qty=0,
                                   exit_plan={}, sizing=t.sizing, opened_at=t.opened_at, snapshot_ref=t.snapshot_ref)
                s.add(row)
            row.state = t.state
            row.open_qty = t.exit_state.qty_open
            row.qty = t.filled_qty or t.sizing.get("qty", 0)
            row.entry_avg = t.entry_avg
            row.exit_avg = round(t.exit_value / t.exit_qty, 2) if t.exit_qty else None
            row.exit_plan = t.exit_state.to_dict()
            row.gross_pnl = round(t.realised_gross, 2)
            row.exit_reason = t.exit_reason
            row.premium_high, row.premium_low = t.premium_high, t.premium_low
            row.underlying_high, row.underlying_low = t.u_high, t.u_low
            row.closed_at = t.closed_at
            row.journal = {**(row.journal or {}), "events": t.events[-200:],
                           "awaiting_confirmation": t.awaiting_confirmation}
            s.commit()
        with self.Session() as s:
            r = s.get(dbm.SecureExitRow, t.id)
            if r is None:
                r = dbm.SecureExitRow(trade_id=t.id, state={})
                s.add(r)
            r.state = t.secure.to_dict()
            r.updated_at = self.clock()
            s.commit()

    def _save_order(self, o: BrokerOrder):
        with self.Session() as s:
            row = s.execute(select(dbm.OrderRow).where(dbm.OrderRow.client_order_id == o.request.client_order_id)).scalar_one_or_none()
            if row is None:
                row = dbm.OrderRow(id=uuid.uuid4().hex, client_order_id=o.request.client_order_id, trade_id=o.request.trade_id,
                                   mode=self.mode, side=o.request.side, security_id=o.request.security_id,
                                   exchange_segment=o.request.exchange_segment, qty=o.request.qty, order_type=o.request.order_type,
                                   limit_price=o.request.limit_price, status=o.status.value, purpose=o.request.purpose,
                                   created_at=o.created_at or self.clock())
                s.add(row)
                s.flush()
            row.broker_order_id = o.broker_order_id
            row.status = o.status.value
            row.filled_qty = o.filled_qty
            row.avg_price = o.avg_price
            row.reason = o.reason
            row.raw = o.raw
            row.updated_at = self.clock()
            for f in o.fills:
                if not s.execute(select(dbm.FillRow).where(dbm.FillRow.fill_id == f.fill_id)).first():
                    s.add(dbm.FillRow(order_id=row.id, fill_id=f.fill_id, qty=f.qty, price=f.price, ts=f.ts))
            s.commit()

    def _save_paper_account(self):
        if isinstance(self.broker, PaperBroker):
            with self.Session() as s:
                row = s.get(dbm.SettingRow, "paper_account")
                if row is None:
                    row = dbm.SettingRow(key="paper_account", value={})
                    s.add(row)
                row.value = {"capital": self.paper_capital, "cash": self.broker.cash}
                row.updated_at = self.clock()
                s.commit()

    # ------------------------------------------------------------------ restart recovery
    def load_state(self):
        now = self.clock()
        with self.Session() as s:
            pa = s.get(dbm.SettingRow, "paper_account")
            if pa and isinstance(self.broker, PaperBroker):
                self.paper_capital = pa.value.get("capital", self.paper_capital)
                self.broker.cash = pa.value.get("cash", self.paper_capital)
            ds = s.get(dbm.DailyStateRow, (now.date(), self.mode))
            self.daily = DailyState.from_dict(ds.state) if ds else DailyState(now.date())
            sigs = s.execute(select(dbm.SignalRow).where(dbm.SignalRow.day == now.date())).scalars().all()
            for r in sigs:
                d = r.data
                sig = Signal(id=d["id"], index=d["index"], direction=d["direction"], setup=d["setup"],
                             confirm_ts=datetime.fromisoformat(d["confirm_ts"]), created_at=datetime.fromisoformat(d["created_at"]),
                             expires_at=datetime.fromisoformat(d["expires_at"]), plan=d["plan"], premium=d["premium"],
                             option=d["option"], score=d["score"], mandatory=d["mandatory"], invalidation=d["invalidation"],
                             strategy_version=d["strategy_version"], params_hash=d["params_hash"],
                             snapshot_ref=d.get("snapshot_ref"), state=SignalState(d["state"]),
                             candles_seen=d.get("candles_seen", 0), history=d.get("history", []))
                self.book.signals[sig.id] = sig
            open_rows = s.execute(select(dbm.TradeRow).where(dbm.TradeRow.state.in_(
                ["PENDING_ENTRY", "OPEN", "CLOSING", "AWAITING_CONFIRMATION"]))).scalars().all()
            for r in open_rows:
                ep = r.exit_plan
                es = ExitPlanState(**{k: ep[k] for k in ExitPlanState.__dataclass_fields__ if k in ep})
                sr = s.get(dbm.SecureExitRow, r.id)
                sec = sx.SecureExitState(**sr.state) if sr else sx.new_state(r.id, self.paper_capital, self.secure_cfg)
                t = TradeRuntime(id=r.id, signal_id=r.signal_id, index=r.index, mode=r.mode, direction=r.direction,
                                 setup=r.setup, option=r.option, lot_size=es.lot_size, sizing=r.sizing, exit_state=es,
                                 secure=sec, state=r.state, entry_avg=r.entry_avg, filled_qty=r.qty if r.entry_avg else 0,
                                 realised_gross=r.gross_pnl, premium_high=r.premium_high, premium_low=r.premium_low,
                                 u_high=r.underlying_high, u_low=r.underlying_low, opened_at=r.opened_at,
                                 snapshot_ref=r.snapshot_ref, events=(r.journal or {}).get("events", []))
                # outstanding orders at restart => state is uncertain until reconciled
                orders = s.execute(select(dbm.OrderRow).where(dbm.OrderRow.trade_id == r.id)).scalars().all()
                for o in orders:
                    if o.side == "SELL":
                        t.exit_order_count += 1
                        for fr in s.execute(select(dbm.FillRow).where(dbm.FillRow.order_id == o.id)).scalars().all():
                            t.processed_fills.add(fr.fill_id)
                            t.exit_qty += fr.qty
                            t.exit_value += fr.price * fr.qty
                    if o.status not in ("FILLED", "REJECTED", "CANCELLED"):
                        if r.mode == "PAPER":
                            # paper orders are in-process only: anything unfilled at crash time never executed
                            o.status, o.reason = "CANCELLED", "Paper order lost at restart (not executed)"
                        else:
                            self.flags.order_state_uncertain = True
                s.commit()
                if t.state == "PENDING_ENTRY" and r.mode == "PAPER" and not t.entry_avg:
                    t.state = "ENTRY_FAILED"
                    t.exit_reason = "Entry not filled before restart"
                    self.daily.active[t.index] = None
                    self._save_trade(t)
                    continue
                if t.state == "CLOSING":
                    t.state = "OPEN"     # re-arm exit logic; position qty is from confirmed fills
                self.trades[t.id] = t
                if t.state == "OPEN":
                    self.daily.active[t.index] = t.id
        self.emit("recovered", {"open_trades": list(self.trades), "pending_signals": [x.id for x in self.book.pending()],
                                "order_state_uncertain": self.flags.order_state_uncertain})
        if self.flags.order_state_uncertain:
            self.sys_event("WARN", "recovery", "Live orders with unknown state found at restart; entries blocked until reconciled")

    # ------------------------------------------------------------------ main loop
    def rollover(self, now: datetime):
        if self.daily.day != now.date():
            self.daily = DailyState(now.date())
            self.book = SignalBook()
            self.research_watch.clear()
            self._save_daily()

    def build_context(self, index: str, now: datetime) -> MarketContext:
        c5 = self.provider.candles(index, 5)
        c15 = self.provider.candles(index, 15)
        cur, prev = self.provider.chain(index)
        tick = self.provider.tick(index)
        st = self.provider.status()
        q = check_quality(now, c5, cur, tick, self.p, require_tick=self.require_tick, feed_faults=list(st.faults))
        gate = risk_gate(self.daily, index, now, self.p, self.flags)
        if not self.entries_enabled:
            gate = {"ok": False, "reasons": gate["reasons"] + ["New entries disabled by user"]}
        dp = self.provider.daily_prev(index, now.date())
        fpc, foc = self.provider.futures_oi_change(index)
        return MarketContext(index, c5, c15, cur, prev, dp, now, q, gate,
                             underlying_prev_close=dp.close if dp else None, fut_price_change=fpc, fut_oi_change=foc)

    def step(self):
        now = self.clock()
        self.rollover(now)
        for index in INDICES:
            c5 = [c for c in self.provider.candles(index, 5) if c.complete]
            if c5 and c5[-1].ts != self.last_candle[index]:
                self.last_candle[index] = c5[-1].ts
                self.on_candle_close(index, now)
            t = self.provider.tick(index)
            if t is not None:
                self.on_tick(index, t, now)
        self.eod()
        self.process_orders(now)
        self.track_research(now)
        self.check_daily_loss(now)

    def on_candle_close(self, index: str, now: datetime):
        ctx = self.build_context(index, now)
        last = [c for c in ctx.c5 if c.complete]
        if not last:
            return
        close_ts = last[-1].ts + timedelta(minutes=5)
        for sig in self.book.on_candle_close(index, close_ts, lambda s_: still_valid(s_, ctx, self.p)):
            self._save_signal(sig)
            self._save_research(f"{sig.id}|{sig.state.value}", index, sig.state.value, sig.score["total"],
                                [sig.history[-1]["reason"]], sig.to_dict(), sig.snapshot_ref, sig.confirm_ts.date())
            self.emit("signal", sig.to_dict())
        res = evaluate(ctx, self.p)
        self.last_eval[index] = {**res.to_dict(), "quality": {"ok": ctx.quality.ok, "reasons": ctx.quality.reasons},
                                 "risk_gate": ctx.risk_gate, "evaluated_at": now.isoformat()}
        if res.signal is not None:
            if self.book.add(res.signal):
                self._save_snapshot(res, res.signal.snapshot_ref)
                self._save_signal(res.signal)
                self.emit("signal", res.signal.to_dict())
        elif res.rejected is not None:
            self._save_snapshot(res, res.rejected["snapshot_ref"])
            self._save_research(res.rejected["id"] + "|REJECTED", index, "REJECTED", res.rejected["score"],
                                res.rejected["reasons"], res.rejected["candidate"], res.rejected["snapshot_ref"], now.date())
        self.emit("evaluation", {"index": index, "state": res.state, "messages": res.messages})
        # open-trade candle logic: trailing and structure invalidation
        for t in list(self.trades.values()):
            if t.index != index or t.state != "OPEN":
                continue
            c5 = last[-HISTORY_BARS_5M:]
            e9 = ind.ema([c.close for c in c5], self.p.ema_fast)[-1]
            a = ind.atr(c5, self.p.atr_len)[-1]
            piv = confirmed_pivots(c5, self.p.pivot_len)
            stt = structure_state(c5, piv, len(c5) - 1)
            entry_ts = t.opened_at
            broken = False
            if t.direction == "BULLISH" and stt.last_low and stt.bearish_breakdown and stt.last_low.confirm_ts >= entry_ts - timedelta(hours=1):
                broken = last[-1].close < stt.last_low.price
            if t.direction == "BEARISH" and stt.last_high and stt.bullish_breakout and stt.last_high.confirm_ts >= entry_ts - timedelta(hours=1):
                broken = last[-1].close > stt.last_high.price
            acts = exit_on_close(t.exit_state, last[-1].close, e9, a, broken, now, self.p)
            for act in acts:
                self.place_exit(t, act.qty, act.reason, "EXIT_ALL", now)
            self._save_trade(t)

    def on_tick(self, index: str, tick: Tick, now: datetime):
        if (now - tick.received_at).total_seconds() > self.p.max_tick_age_s:
            return  # never act on a stale tick
        for sig in self.book.on_price(index, tick.ltp, tick.feed_ts or tick.received_at):
            self._save_signal(sig)
            self.emit("signal", sig.to_dict())
            self.execute_entry(sig, now)
        for t in list(self.trades.values()):
            if t.index == index and t.state == "OPEN":
                self.manage_open(t, tick.ltp, now)

    # ------------------------------------------------------------------ entries
    def pretrade_checks(self, sig: Signal, now: datetime) -> tuple[bool, list[str], dict]:
        ctx = self.build_context(sig.index, now)
        reasons = []
        gate = risk_gate(self.daily, sig.index, now, self.p, self.flags, signal_id=sig.id)
        if not gate["ok"]:
            reasons += gate["reasons"]
        if not self.entries_enabled:
            reasons.append("New entries disabled by user")
        if not ctx.quality.ok:
            reasons += ctx.quality.reasons
        if sig.state != SignalState.TRIGGERED or now >= sig.expires_at:
            reasons.append("Signal no longer valid")
        sec = (sig.option or {}).get("security_id")
        info = self.provider.instrument(sec) if sec else None
        q = self.provider.option_quote(sec) if sec else None
        summary: dict = {}
        if info is None:
            reasons.append("Contract not found in verified instrument master")
        else:
            if info.option_type != sig.option.get("option_type") or (info.strike is not None and float(info.strike) != float(sig.option["strike"])):
                reasons.append("Instrument identity mismatch (type/strike)")
            cur, _ = self.provider.chain(sig.index)
            if cur is not None and info.expiry is not None and info.expiry != cur.expiry:
                reasons.append("Contract expiry differs from active chain expiry")
            if info.expiry is not None and info.expiry < now.date():
                reasons.append("Contract expired")
        if q is None or (now - q[1]).total_seconds() > self.p.max_tick_age_s * 2:
            reasons.append("No fresh option quote")
        if reasons:
            return False, reasons, summary
        quote = q[0]
        prem = premium_plan(quote.to_dict(), sig.plan["risk_points"], self.p)
        if prem is None:
            return False, ["Premium plan unavailable (missing Greeks/bid/ask)"], summary
        bal = self.broker.available_balance() if self.mode == "PAPER" else (self.live_broker.available_balance() if self.live_broker else None)
        if bal is None:
            return False, ["Available balance unavailable from broker"], summary
        loss_left = self.secure_cfg.max_daily_loss + min(0.0, self.daily.realised_gross)
        t1_est = prem["entry_premium_est"] + abs(quote.delta or 0) * sig.plan["risk_points"]
        sz = size_position(bal, prem["entry_premium_est"], prem["premium_sl"], info.lot_size,
                           self.secure_cfg.max_loss_per_trade, loss_left, now.date(), self.schedule, t1_premium_est=t1_est)
        summary = {
            "index": sig.index, "option_type": info.option_type, "expiry": info.expiry.isoformat() if info.expiry else None,
            "strike": info.strike, "security_id": info.security_id, "trading_symbol": info.trading_symbol,
            "lots": sz.lots, "lot_size": info.lot_size, "qty": sz.qty, "premium": prem["entry_premium_est"],
            "bid": quote.bid, "ask": quote.ask, "capital_required": sz.capital_required, "est_charges": sz.est_charges,
            "max_risk": sz.max_loss, "premium_sl": prem["premium_sl"], "underlying_sl": sig.plan["stop"],
            "targets": {"t1": sig.plan["t1"], "t2": sig.plan["t2"], "t3": sig.plan["t3"]}, "rr_t1": sz.rr_t1,
            "available_balance": bal, "exit_control": prem["controls_exit"], "mode": self.mode,
            "sizing": sz.to_dict(), "premium_plan": prem,
        }
        if not sz.ok:
            return False, sz.reasons, summary
        return True, [], summary

    def execute_entry(self, sig: Signal, now: datetime, confirmed_by_user: bool = False):
        ok, reasons, summary = self.pretrade_checks(sig, now)
        if not ok:
            sig.transition(SignalState.CANCELLED, now, "Pre-trade check failed: " + "; ".join(reasons))
            self._save_signal(sig)
            self._save_research(f"{sig.id}|CANCELLED", sig.index, "CANCELLED", sig.score["total"], reasons,
                                {**sig.to_dict(), "pretrade_summary": summary}, sig.snapshot_ref, sig.confirm_ts.date())
            self.emit("signal", sig.to_dict())
            return None
        tid = f"T{now.strftime('%Y%m%d')}-{sig.index}-{uuid.uuid4().hex[:8]}"
        prem = summary["premium_plan"]
        es = ExitPlanState(direction=sig.direction, entry_underlying=sig.plan["trigger"], stop=sig.plan["stop"],
                           initial_stop=sig.plan["stop"], t1=sig.plan["t1"], t2=sig.plan["t2"], t3=sig.plan["t3"],
                           premium_sl=prem["premium_sl"], qty_total=summary["qty"], qty_open=0, lot_size=summary["lot_size"])
        cap = self.broker.available_balance() or self.paper_capital
        t = TradeRuntime(id=tid, signal_id=sig.id, index=sig.index, mode=self.mode, direction=sig.direction, setup=sig.setup,
                         option={**sig.option, "trading_symbol": summary["trading_symbol"], "expiry": summary["expiry"],
                                 "security_id": summary["security_id"]},
                         lot_size=summary["lot_size"], sizing={**summary["sizing"], "pretrade_summary": summary},
                         exit_state=es, secure=sx.new_state(tid, cap, self.secure_cfg), opened_at=now,
                         snapshot_ref=sig.snapshot_ref, expected_entry=summary["premium"])
        self.trades[tid] = t
        self.daily.active[sig.index] = tid          # reserve the index slot immediately (one active position)
        if self.mode == "LIVE" and not confirmed_by_user:
            t.state = "AWAITING_CONFIRMATION"
            t.awaiting_confirmation = True
            t.events.append({"at": now.isoformat(), "event": "awaiting_user_confirmation", "summary": summary})
            self._save_trade(t)
            self._save_daily()
            self.emit("confirm_required", {"trade_id": tid, "summary": summary})
            return t
        self._send_entry(t, now)
        return t

    def confirm_live_trade(self, trade_id: str, actor: str = "user"):
        t = self.trades.get(trade_id)
        if t is None or t.state != "AWAITING_CONFIRMATION":
            return False, "No trade awaiting confirmation"
        sig = self.book.signals.get(t.signal_id)
        now = self.clock()
        # revalidate everything again at confirmation time (data may have changed)
        self.daily.active[t.index] = None
        ok, reasons, _ = self.pretrade_checks(sig, now) if sig else (False, ["Signal missing"], {})
        if not ok:
            t.state, t.exit_reason = "ENTRY_FAILED", "Revalidation at confirmation failed: " + "; ".join(reasons)
            self._save_trade(t)
            self.trades.pop(t.id, None)
            if sig and sig.state == SignalState.TRIGGERED:
                sig.transition(SignalState.CANCELLED, now, t.exit_reason)
                self._save_signal(sig)
            self._save_daily()
            return False, t.exit_reason
        self.daily.active[t.index] = t.id
        self.audit(actor, "live_order_confirmed", {"trade_id": trade_id})
        t.awaiting_confirmation = False
        self._send_entry(t, now)
        return True, "sent"

    def _active_broker(self) -> Broker:
        if self.mode == "LIVE":
            if not (self.live_enabled and self.live_broker is not None):
                raise RuntimeError("Live broker not enabled")
            return self.live_broker
        return self.broker

    def _send_entry(self, t: TradeRuntime, now: datetime):
        req = OrderRequest(client_order_id=f"{t.id}-ENTRY", trade_id=t.id, side="BUY", security_id=t.option["security_id"],
                           exchange_segment="NSE_FNO", qty=t.exit_state.qty_total, order_type="MARKET", purpose="ENTRY")
        t.state = "PENDING_ENTRY"
        try:
            o = self._active_broker().place(req, now)
        except Exception as e:  # network / API failure => uncertain state
            self.flags.order_state_uncertain = True
            o = BrokerOrder(req, OrderStatus.UNKNOWN, reason=f"place failed: {e}", created_at=now, updated_at=now)
            self.sys_event("ERROR", "orders", f"Entry order placement uncertain for {t.id}: {e}")
        t.entry_order = o
        t.events.append({"at": now.isoformat(), "event": "entry_order", "status": o.status.value, "client_id": req.client_order_id})
        self._save_order(o)
        self._save_trade(t)
        self._save_daily()
        self.emit("order", o.to_dict())

    # ------------------------------------------------------------------ order processing
    def process_orders(self, now: datetime):
        for t in list(self.trades.values()):
            br = self.broker if t.mode == "PAPER" else self.live_broker
            if br is None:
                continue
            o = t.entry_order
            if o is not None and t.state == "PENDING_ENTRY":
                before = o.filled_qty
                if o.status != OrderStatus.UNKNOWN:
                    br.refresh(o, now)
                if o.status == OrderStatus.PARTIALLY_FILLED and (now - o.created_at).total_seconds() > ENTRY_FILL_TIMEOUT_S:
                    br.cancel(o, now)
                    t.events.append({"at": now.isoformat(), "event": "entry_remainder_cancelled", "filled": o.filled_qty})
                if o.filled_qty != before or o.status in FINAL:
                    self._on_entry_update(t, o, now)
                self._save_order(o)
            for eo in list(t.exit_orders):
                if eo.status in FINAL:
                    continue
                before = eo.filled_qty
                if eo.status != OrderStatus.UNKNOWN:
                    br.refresh(eo, now)
                if eo.filled_qty != before:
                    self._on_exit_fill(t, eo, eo.filled_qty - before, now)
                if eo.status in (OrderStatus.REJECTED, OrderStatus.CANCELLED) and eo.filled_qty < eo.request.qty:
                    t.events.append({"at": now.isoformat(), "event": "exit_order_failed", "reason": eo.reason})
                    t.exit_retries += 1
                    if t.exit_retries > MAX_EXIT_RETRIES:
                        self.flags.order_state_uncertain = True
                        self.sys_event("ERROR", "orders", f"Exit for {t.id} failed {t.exit_retries} times; manual action required")
                    else:
                        self.sys_event("WARN", "orders", f"Exit order for {t.id} {eo.status.value}: {eo.reason}; retrying")
                        self.place_exit(t, eo.request.qty - eo.filled_qty, (t.exit_reason or "") + " (retry)", eo.request.purpose, now)
                self._save_order(eo)
            if t.state in ("OPEN", "CLOSING"):
                self._save_trade(t)

    def _on_entry_update(self, t: TradeRuntime, o: BrokerOrder, now: datetime):
        sig = self.book.signals.get(t.signal_id)
        if o.filled_qty > 0 and (o.status in FINAL):
            t.entry_avg = o.avg_price
            t.filled_qty = o.filled_qty
            t.exit_state.qty_total = o.filled_qty
            t.exit_state.qty_open = o.filled_qty
            t.state = "OPEN"
            t.premium_high = t.premium_low = o.avg_price
            slip = (o.avg_price - (t.expected_entry or o.avg_price)) * o.filled_qty
            t.events.append({"at": now.isoformat(), "event": "entry_filled", "qty": o.filled_qty, "avg": o.avg_price,
                             "slippage_vs_expected": round(slip, 2), "partial": o.filled_qty < o.request.qty})
            record_entry(self.daily, t.index, t.id, t.signal_id)
            if sig and sig.state == SignalState.TRIGGERED:
                sig.transition(SignalState.EXECUTED, now, f"Entry filled {o.filled_qty} @ {o.avg_price} (trade {t.id})")
                self._save_signal(sig)
            self._save_daily()
            self._save_paper_account()
            self.emit("trade", {"trade_id": t.id, "state": t.state})
        elif o.status in (OrderStatus.REJECTED, OrderStatus.CANCELLED) and o.filled_qty == 0:
            t.state = "ENTRY_FAILED"
            t.exit_reason = f"Entry order {o.status.value}: {o.reason}"
            self.daily.active[t.index] = None
            if sig and sig.state == SignalState.TRIGGERED:
                sig.transition(SignalState.CANCELLED, now, t.exit_reason)
                self._save_signal(sig)
            self._save_trade(t)
            self.trades.pop(t.id, None)
            self._save_daily()
            self.emit("trade", {"trade_id": t.id, "state": t.state, "reason": t.exit_reason})

    def place_exit(self, t: TradeRuntime, qty: int, reason: str, purpose: str, now: datetime):
        pending = [x for x in t.exit_orders if x.status not in FINAL]
        pend_qty = sum(x.remaining for x in pending)
        qty = min(qty, t.exit_state.qty_open - pend_qty)
        if qty <= 0:
            return None  # an exit is already working for this quantity: never duplicate
        t.exit_order_count += 1
        req = OrderRequest(client_order_id=f"{t.id}-EXIT-{t.exit_order_count}", trade_id=t.id, side="SELL",
                           security_id=t.option["security_id"], exchange_segment="NSE_FNO", qty=qty, order_type="MARKET",
                           purpose=purpose)
        try:
            o = (self.broker if t.mode == "PAPER" else self.live_broker).place(req, now)
        except Exception as e:
            self.flags.order_state_uncertain = True
            o = BrokerOrder(req, OrderStatus.UNKNOWN, reason=f"place failed: {e}", created_at=now, updated_at=now)
            self.sys_event("ERROR", "orders", f"Exit order placement uncertain for {t.id}: {e}")
        t.exit_orders.append(o)
        if purpose != "EXIT_PARTIAL":
            t.state = "CLOSING"
            t.exit_reason = t.exit_reason or reason
        t.events.append({"at": now.isoformat(), "event": "exit_order", "qty": qty, "reason": reason, "status": o.status.value})
        self._save_order(o)
        self._save_trade(t)
        self.emit("order", o.to_dict())
        return o

    def _on_exit_fill(self, t: TradeRuntime, o: BrokerOrder, qty: int, now: datetime):
        seen = t.processed_fills
        for f in o.fills:
            if f.fill_id in seen:
                continue
            seen.add(f.fill_id)
            t.exit_qty += f.qty
            t.exit_value += f.price * f.qty
            t.realised_gross += (f.price - (t.entry_avg or 0)) * f.qty
            t.exit_state.qty_open -= f.qty
            t.events.append({"at": now.isoformat(), "event": "exit_fill", "qty": f.qty, "price": f.price,
                             "purpose": o.request.purpose, "fill_id": f.fill_id})
        if t.exit_state.qty_open <= 0:
            self._close_trade(t, now)
        self._save_paper_account()

    def _close_trade(self, t: TradeRuntime, now: datetime):
        t.state = "CLOSED"
        t.closed_at = now
        buy = leg_charges("BUY", (t.entry_avg or 0) * t.filled_qty, 1, now.date(), self.schedule)
        sell = leg_charges("SELL", t.exit_value, max(1, len([o for o in t.exit_orders if o.filled_qty > 0])), now.date(), self.schedule)
        charges = round(buy.total + sell.total, 2)
        net = round(t.realised_gross - charges, 2)
        if isinstance(self.broker, PaperBroker) and t.mode == "PAPER":
            self.broker.deduct_charges(charges)
        record_exit(self.daily, t.index, t.realised_gross, net)
        outcome = self.explain_outcome(t)
        self._save_trade(t)
        with self.Session() as s:
            row = s.get(dbm.TradeRow, t.id)
            row.charges = charges
            row.net_pnl = net
            row.slippage = round(sum(e.get("slippage_vs_expected", 0) for e in t.events if e.get("event") == "entry_filled"), 2)
            row.journal = {**(row.journal or {}), "charges": {"buy": buy.to_dict(), "sell": sell.to_dict()},
                           "outcome": outcome, "gross_pnl": round(t.realised_gross, 2), "net_pnl": net}
            s.commit()
        self.trades.pop(t.id, None)
        self._save_daily()
        self._save_paper_account()
        self.emit("trade", {"trade_id": t.id, "state": "CLOSED", "gross": round(t.realised_gross, 2), "net": net})

    def explain_outcome(self, t: TradeRuntime) -> dict:
        """Explain target/stop outcomes from RECORDED prices only (no guessing)."""
        es = t.exit_state
        bull = t.direction == "BULLISH"
        hi, lo = t.u_high, t.u_low
        out = {"underlying_high_after_entry": hi, "underlying_low_after_entry": lo,
               "premium_high_after_entry": t.premium_high, "premium_low_after_entry": t.premium_low,
               "exit_reason": t.exit_reason, "stop_history": es.stop_history}
        if hi is None or lo is None:
            out["explanation"] = "Insufficient recorded underlying prices after entry."
            return out
        fav, adv = (hi, lo) if bull else (lo, hi)
        lines = []
        for name, lvl in (("T1", es.t1), ("T2", es.t2), ("T3", es.t3)):
            if lvl is None:
                lines.append(f"{name}: not set (next S/R did not allow it)")
                continue
            hit = fav >= lvl if bull else fav <= lvl
            lines.append(f"{name} {lvl}: {'reached' if hit else 'not reached'} (best recorded underlying {fav})")
        sl_hit = adv <= es.initial_stop if bull else adv >= es.initial_stop
        lines.append(f"Initial stop {es.initial_stop}: {'touched' if sl_hit else 'not touched'} (worst recorded underlying {adv})")
        out["explanation"] = "; ".join(lines)
        return out

    # ------------------------------------------------------------------ open position management
    def manage_open(self, t: TradeRuntime, underlying: float, now: datetime):
        t.u_high = underlying if t.u_high is None else max(t.u_high, underlying)
        t.u_low = underlying if t.u_low is None else min(t.u_low, underlying)
        q = self.provider.option_quote(t.option["security_id"])
        bid = None
        delta = None
        if q is not None and (now - q[1]).total_seconds() <= self.p.max_tick_age_s * 2:
            bid = q[0].bid
            delta = q[0].delta
            if q[0].ltp:
                t.premium_high = max(t.premium_high or q[0].ltp, q[0].ltp)
                t.premium_low = min(t.premium_low or q[0].ltp, q[0].ltp)
        cost_per_unit = 0.0
        if t.filled_qty:
            cost_per_unit = round_trip_estimate(t.entry_avg or 0, t.entry_avg or 0, t.filled_qty, now.date(), self.schedule) / t.filled_qty \
                + self.p.slippage_ticks * self.p.tick_size * 2
        if bid is not None and t.entry_avg is not None:
            gross_open = (bid - t.entry_avg) * t.exit_state.qty_open + t.realised_gross
            reason = sx.update(t.secure, gross_open, self.secure_cfg)
            if reason:
                self.place_exit(t, t.exit_state.qty_open, reason, "EXIT_ALL", now)
                return
        for act in exit_on_price(t.exit_state, underlying, bid, now, self.p, delta, cost_per_unit):
            if act.kind == "PARTIAL_T1":
                self.place_exit(t, act.qty, act.reason, "EXIT_PARTIAL", now)
            else:
                self.place_exit(t, act.qty, act.reason, "EXIT_ALL", now)

    def check_daily_loss(self, now: datetime):
        unreal = 0.0
        for t in self.trades.values():
            if t.state in ("OPEN", "CLOSING") and t.entry_avg is not None:
                q = self.provider.option_quote(t.option["security_id"])
                if q and q[0].bid:
                    unreal += (q[0].bid - t.entry_avg) * t.exit_state.qty_open
        if sx.daily_breach(self.daily.realised_gross, unreal, self.secure_cfg) and not self.daily.blocked_reason:
            self.daily.blocked_reason = f"Daily loss limit ₹{self.secure_cfg.max_daily_loss:,.0f} reached"
            for t in list(self.trades.values()):
                if t.state == "OPEN":
                    self.place_exit(t, t.exit_state.qty_open, "SECURE_DAILY_MAX_LOSS", "EXIT_ALL", now)
            self._save_daily()
            self.sys_event("WARN", "risk", self.daily.blocked_reason)

    def track_research(self, now: datetime):
        for rid, w in list(self.research_watch.items()):
            q = self.provider.option_quote(w["security_id"])
            if not q or not q[0].ltp:
                continue
            px = q[0].ltp
            w["hi"] = px if w["hi"] is None else max(w["hi"], px)
            w["lo"] = px if w["lo"] is None else min(w["lo"], px)
            with self.Session() as s:
                r = s.get(dbm.ResearchLog, rid)
                if r is not None:
                    r.premium_high_after, r.premium_low_after = w["hi"], w["lo"]
                    s.commit()

    # ------------------------------------------------------------------ controls
    def kill_switch(self, actor: str = "user"):
        self.flags.kill_switch = True
        now = self.clock()
        for t in list(self.trades.values()):
            if t.state == "OPEN":
                self.place_exit(t, t.exit_state.qty_open, "KILL_SWITCH", "EXIT_ALL", now)
            elif t.state == "AWAITING_CONFIRMATION":
                t.state, t.exit_reason = "ENTRY_FAILED", "Kill switch before confirmation"
                self.daily.active[t.index] = None
                self._save_trade(t)
                self.trades.pop(t.id, None)
        for s_ in self.book.pending():
            s_.transition(SignalState.CANCELLED, now, "Kill switch")
            self._save_signal(s_)
        self.audit(actor, "kill_switch", {"open_trades": list(self.trades)})
        self.sys_event("WARN", "control", "KILL SWITCH engaged: entries blocked, exits sent")

    def reset_kill_switch(self, actor: str):
        self.flags.kill_switch = False
        self.audit(actor, "kill_switch_reset", {})

    def eod(self):
        now = self.clock()
        if now.time() >= self.p.squareoff_time:
            for t in list(self.trades.values()):
                if t.state == "OPEN":
                    self.place_exit(t, t.exit_state.qty_open, "EOD square-off", "EXIT_ALL", now)

    def reset_paper(self, actor: str, confirm_text: str, capital: Optional[float] = None) -> tuple[bool, str]:
        if confirm_text != "RESET PAPER ACCOUNT":
            return False, "Confirmation text mismatch"
        if any(t.state in ("OPEN", "CLOSING", "PENDING_ENTRY") for t in self.trades.values()):
            return False, "Close open paper positions first"
        if self.mode != "PAPER":
            return False, "Only available in paper mode"
        old = {"capital": self.paper_capital, "cash": self.broker.available_balance()}
        self.paper_capital = capital or self.paper_capital
        self.broker = PaperBroker(self._quote_for_broker, self.paper_capital, tick=self.p.tick_size,
                                  slippage_ticks=self.p.slippage_ticks)
        self._save_paper_account()
        self.audit(actor, "paper_reset", {"old": old, "new_capital": self.paper_capital})
        return True, "Paper account reset"
