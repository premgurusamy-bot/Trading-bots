"""Strict parsers for Dhan responses. Malformed/incomplete payloads raise ParseError; missing optional
fields stay None (never fabricated).

Schema provenance:
* WebSocket binary packet layouts: VERIFIED from official SDK v2.3.0 `marketfeed.py` (struct formats copied).
* Option chain JSON, intraday arrays, expiry list: from Dhan v2 documentation as known to the implementer;
  UNVERIFIED in this build environment. Use the "Capture diagnostic" button in System Health to store a
  sanitized live response and run tests/test_dhan_parsers.py against it.
"""
from __future__ import annotations

import csv
import hashlib
import io
import struct
from datetime import datetime, date, timezone
from typing import Optional

from app.core.types import IST, Candle, OptionChainSnapshot, OptionQuote, OptionType, Index


class ParseError(ValueError):
    pass


def _f(x) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise ParseError(f"non-numeric value {x!r}")
    return v


def parse_option_chain(resp: dict, index: Index, expiry: date, received_at: datetime) -> OptionChainSnapshot:
    if not isinstance(resp, dict) or "data" not in resp:
        raise ParseError("option chain: missing 'data'")
    d = resp["data"]
    if not isinstance(d, dict) or "oc" not in d or "last_price" not in d:
        raise ParseError("option chain: missing 'oc' or 'last_price'")
    spot = _f(d["last_price"])
    if spot is None or spot <= 0:
        raise ParseError("option chain: invalid last_price")
    oc = d["oc"]
    if not isinstance(oc, dict) or not oc:
        raise ParseError("option chain: empty 'oc'")
    rows: list[OptionQuote] = []
    for k, sides in oc.items():
        strike = _f(k)
        if strike is None or strike <= 0 or not isinstance(sides, dict):
            raise ParseError(f"option chain: bad strike row {k!r}")
        for key, ot in (("ce", OptionType.CE), ("pe", OptionType.PE)):
            s = sides.get(key)
            if not s:
                continue
            g = s.get("greeks") or {}
            has_g = all(g.get(x) is not None for x in ("delta", "gamma", "theta"))
            iv = _f(s.get("implied_volatility"))
            rows.append(OptionQuote(
                strike=strike, option_type=ot,
                security_id=str(s["security_id"]) if s.get("security_id") is not None else None,
                ltp=_f(s.get("last_price")), oi=_f(s.get("oi")), prev_oi=_f(s.get("previous_oi")),
                volume=_f(s.get("volume")), prev_close=_f(s.get("previous_close_price")),
                bid=_f(s.get("top_bid_price")), bid_qty=_f(s.get("top_bid_quantity")),
                ask=_f(s.get("top_ask_price")), ask_qty=_f(s.get("top_ask_quantity")),
                iv=iv if (iv is not None and iv > 0) else None,
                delta=_f(g.get("delta")) if has_g else None, gamma=_f(g.get("gamma")) if has_g else None,
                theta=_f(g.get("theta")) if has_g else None, vega=_f(g.get("vega")) if has_g else None,
                greeks_source="feed" if has_g else None,
            ))
    if not rows:
        raise ParseError("option chain: no CE/PE rows")
    # Dhan returns zero greeks/prices for untraded strikes: treat zero LTP & zero bid/ask as missing
    for r in rows:
        if r.ltp == 0:
            r.ltp = None
        if r.bid == 0:
            r.bid = None
        if r.ask == 0:
            r.ask = None
    return OptionChainSnapshot(index, expiry, spot, rows, "dhan:/optionchain", None, received_at)


def parse_expiry_list(resp: dict) -> list[date]:
    if not isinstance(resp, dict) or not isinstance(resp.get("data"), list):
        raise ParseError("expiry list: missing 'data' list")
    out = []
    for x in resp["data"]:
        try:
            out.append(date.fromisoformat(str(x)[:10]))
        except ValueError:
            raise ParseError(f"expiry list: bad date {x!r}")
    return sorted(out)


def parse_candles(resp: dict, source: str, minutes: int, now: Optional[datetime] = None) -> list[Candle]:
    """Dhan chart arrays: open/high/low/close/volume/timestamp (epoch seconds), optional open_interest.
    The last candle is marked incomplete if its period has not ended at `now`."""
    req = ("open", "high", "low", "close", "volume", "timestamp")
    if not isinstance(resp, dict) or any(k not in resp for k in req):
        raise ParseError(f"candles: missing one of {req}")
    n = len(resp["timestamp"])
    if any(len(resp[k]) != n for k in req):
        raise ParseError("candles: array length mismatch")
    oi = resp.get("open_interest")
    if oi is not None and len(oi) != n:
        raise ParseError("candles: open_interest length mismatch")
    out = []
    from datetime import timedelta
    for i in range(n):
        ts = datetime.fromtimestamp(float(resp["timestamp"][i]), tz=timezone.utc).astimezone(IST)
        complete = True if now is None else (ts + timedelta(minutes=minutes) <= now)
        try:
            out.append(Candle(ts=ts, open=float(resp["open"][i]), high=float(resp["high"][i]), low=float(resp["low"][i]),
                              close=float(resp["close"][i]), volume=float(resp["volume"][i] or 0), complete=complete,
                              source=source, oi=float(oi[i]) if oi is not None and oi[i] is not None else None))
        except ValueError as e:
            raise ParseError(f"candles: {e}")
    if any(out[i].ts >= out[i + 1].ts for i in range(len(out) - 1)):
        raise ParseError("candles: timestamps not strictly increasing")
    return out


# ---------------- WebSocket v2 binary packets (layouts from official SDK) ----------------
SEG = {0: "IDX_I", 1: "NSE_EQ", 2: "NSE_FNO", 3: "NSE_CURRENCY", 4: "BSE_EQ", 5: "MCX_COMM", 7: "BSE_CURRENCY", 8: "BSE_FNO"}


def parse_ws_packet(data: bytes) -> Optional[dict]:
    if not data:
        return None
    code = data[0]
    try:
        if code == 2:       # ticker
            _, _, seg, sid, ltp, ltt = struct.unpack("<BHBIfI", data[0:16])
            return {"type": "ticker", "segment": SEG.get(seg, str(seg)), "security_id": str(sid), "ltp": round(ltp, 2),
                    "ltt": datetime.fromtimestamp(ltt, tz=timezone.utc)}
        if code == 4:       # quote
            u = struct.unpack("<BHBIfHIfIIIffff", data[0:50])
            return {"type": "quote", "segment": SEG.get(u[2], str(u[2])), "security_id": str(u[3]), "ltp": round(u[4], 2),
                    "ltq": u[5], "ltt": datetime.fromtimestamp(u[6], tz=timezone.utc), "atp": u[7], "volume": u[8],
                    "total_sell_qty": u[9], "total_buy_qty": u[10], "open": u[11], "close": u[12], "high": u[13], "low": u[14]}
        if code == 5:       # OI
            _, _, seg, sid, oi = struct.unpack("<BHBII", data[0:12])
            return {"type": "oi", "segment": SEG.get(seg, str(seg)), "security_id": str(sid), "oi": oi}
        if code == 6:       # previous close
            _, _, seg, sid, pc, poi = struct.unpack("<BHBIfI", data[0:16])
            return {"type": "prev_close", "segment": SEG.get(seg, str(seg)), "security_id": str(sid), "prev_close": pc, "prev_oi": poi}
        if code == 8:       # full
            u = struct.unpack("<BHBIfHIfIIIIIIffff100s", data[0:162])
            depth = []
            for i in range(5):
                bq, aq, bo, ao, bp, ap = struct.unpack("<IIHHff", u[18][i * 20:(i + 1) * 20])
                depth.append({"bid_qty": bq, "ask_qty": aq, "bid_orders": bo, "ask_orders": ao, "bid": bp, "ask": ap})
            return {"type": "full", "segment": SEG.get(u[2], str(u[2])), "security_id": str(u[3]), "ltp": round(u[4], 2),
                    "ltq": u[5], "ltt": datetime.fromtimestamp(u[6], tz=timezone.utc), "atp": u[7], "volume": u[8],
                    "total_sell_qty": u[9], "total_buy_qty": u[10], "oi": u[11], "oi_high": u[12], "oi_low": u[13],
                    "open": u[14], "close": u[15], "high": u[16], "low": u[17], "depth": depth}
        if code == 50:      # server disconnection
            reason = struct.unpack("<BHBIH", data[0:10])[4] if len(data) >= 10 else None
            return {"type": "disconnect", "reason_code": reason}
        if code == 7:
            return {"type": "status"}
    except struct.error as e:
        raise ParseError(f"ws packet code {code}: {e}")
    return {"type": "unknown", "code": code}


# ---------------- instrument master (compact CSV) ----------------
COMPACT_COLS = {
    "exch": "SEM_EXM_EXCH_ID", "segment": "SEM_SEGMENT", "security_id": "SEM_SMST_SECURITY_ID",
    "instrument": "SEM_INSTRUMENT_NAME", "symbol": "SEM_TRADING_SYMBOL", "lot": "SEM_LOT_UNITS",
    "expiry": "SEM_EXPIRY_DATE", "strike": "SEM_STRIKE_PRICE", "option_type": "SEM_OPTION_TYPE",
    "tick": "SEM_TICK_SIZE",
}


def parse_instrument_master(raw: bytes, underlyings=("NIFTY", "BANKNIFTY")) -> tuple[list[dict], dict]:
    """Returns (rows for index options/futures/indices of the given underlyings, metadata). Fails loudly if
    the required columns are absent (format change) instead of guessing."""
    text = raw.decode("utf-8-sig")
    rdr = csv.DictReader(io.StringIO(text))
    cols = rdr.fieldnames or []
    missing = [v for v in COMPACT_COLS.values() if v not in cols]
    if missing:
        raise ParseError(f"instrument master: missing columns {missing}; got {cols[:20]}")
    out = []
    for r in rdr:
        if r[COMPACT_COLS["exch"]] != "NSE":
            continue
        ins = r[COMPACT_COLS["instrument"]].strip()
        sym = r[COMPACT_COLS["symbol"]].strip()
        und = sym.split("-")[0]
        if ins not in ("OPTIDX", "FUTIDX", "INDEX") or und not in underlyings:
            continue
        exp = r[COMPACT_COLS["expiry"]].strip()
        try:
            expd = date.fromisoformat(exp[:10]) if exp and not exp.startswith("0001") else None
        except ValueError:
            expd = None
        ot = r[COMPACT_COLS["option_type"]].strip()
        lot = r[COMPACT_COLS["lot"]].strip()
        out.append({
            "security_id": r[COMPACT_COLS["security_id"]].strip(),
            "exchange_segment": "IDX_I" if ins == "INDEX" else "NSE_FNO", "instrument": ins, "underlying": und,
            "trading_symbol": sym, "expiry": expd, "strike": _f(r[COMPACT_COLS["strike"]]) if ins == "OPTIDX" else None,
            "option_type": ot if ot in ("CE", "PE") else None, "lot_size": int(float(lot)) if lot else 0,
            "tick_size": _f(r[COMPACT_COLS["tick"]]),
        })
    meta = {"sha256": hashlib.sha256(raw).hexdigest(), "row_count": len(out), "columns": cols}
    if not out:
        raise ParseError("instrument master: no NIFTY/BANKNIFTY rows found")
    return out, meta
