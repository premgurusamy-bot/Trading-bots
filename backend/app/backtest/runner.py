"""Backtest runner: fetches GENUINE historical data from Dhan, runs the simulator, splits development /
validation / out-of-sample periods by date (no parameter optimisation is performed on any period), and
stores the run with full data provenance."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from app import db as dbm
from app.backtest.metrics import trade_metrics
from app.backtest.sim import run_backtest
from app.core.types import IST
from app.data.dhan.parsers import parse_candles, ParseError
from app.strategy.candles import resample
from app.strategy.engine import merge_futures_volume
from app.strategy.params import STRATEGY_VERSION

CHUNK_DAYS = 85   # conservative request window for intraday history (official max UNVERIFIED here)


def fetch_intraday(client, security_id, segment, instrument, frm: date, to: date, oi=False):
    out = []
    cur = frm
    while cur <= to:
        end = min(cur + timedelta(days=CHUNK_DAYS), to)
        raw = client.intraday(security_id, segment, instrument, 5, cur.isoformat(), (end + timedelta(days=1)).isoformat(), oi)
        out += parse_candles(raw, f"dhan:intraday:{segment}:{security_id}", 5)
        cur = end + timedelta(days=1)
    seen, dedup = set(), []
    for c in out:
        if c.ts not in seen:
            seen.add(c.ts)
            dedup.append(c)
    return sorted(dedup, key=lambda c: c.ts)


def fetch_atm_premiums(client, spot_id: str, frm: date, to: date, flag: str) -> tuple[dict, list[str]]:
    """Genuine ATM CE/PE 5m closes from the rolling-option endpoint (response schema UNVERIFIED: parsed
    defensively; on mismatch the overlay is skipped and the reason reported)."""
    notes, out = [], {"CE": {}, "PE": {}}
    for side, opt in (("CE", "CALL"), ("PE", "PUT")):
        cur = frm
        while cur <= to:
            end = min(cur + timedelta(days=28), to)
            try:
                raw = client.rolling_option(spot_id, flag, 1, "ATM", opt, cur.isoformat(), (end + timedelta(days=1)).isoformat(), 5)
                data = raw.get("data", raw) if isinstance(raw, dict) else None
                node = (data or {}).get("ce" if side == "CE" else "pe") or {}
                if not node or "timestamp" not in node:
                    raise ParseError("rolling option: unexpected schema")
                for ts, cl in zip(node["timestamp"], node["close"]):
                    out[side][datetime.fromtimestamp(float(ts), tz=IST)] = float(cl)
            except Exception as e:
                notes.append(f"{side} {cur}..{end}: {type(e).__name__}: {str(e)[:120]}")
                return {}, notes
            cur = end + timedelta(days=1)
    return out, notes


def run_from_dhan(rt, index: str, frm: date, to: date, split=(0.6, 0.2, 0.2)) -> dict:
    prov = rt.provider
    ids = getattr(prov, "_ids", {}).get(index)
    if not ids:
        raise RuntimeError("Instrument identifiers not resolved (Dhan feed not started)")
    client = rt.dhan_client
    spot = fetch_intraday(client, ids["spot"], "IDX_I", "INDEX", frm, to)
    fut = []
    fut_note = None
    if ids.get("fut"):
        try:
            fut = fetch_intraday(client, ids["fut"], "NSE_FNO", "FUTIDX", frm, to)
        except Exception as e:
            fut_note = f"futures volume unavailable: {type(e).__name__}"
    c5 = merge_futures_volume(spot, fut)
    vol_cov = sum(1 for c in c5 if c.volume > 0) / len(c5) if c5 else 0
    flag = "MONTH" if index == "BANKNIFTY" else "WEEK"
    prem, prem_notes = fetch_atm_premiums(client, ids["spot"], frm, to, flag)
    lot = None
    for info in prov._instr.values():
        if info.trading_symbol.startswith(index + "-"):
            lot = info.lot_size
            break
    return run_and_store(rt, index, c5, prem or None, lot, {
        "source": "Dhan /charts/intraday (5m) + /charts/rollingoption (ATM premiums)",
        "spot_candles": len(spot), "futures_candles": len(fut), "volume_coverage": round(vol_cov, 4),
        "futures_note": fut_note or ("Expired futures contracts are not fetched: volume exists only for the current contract's life"
                                     if vol_cov < 0.95 else None),
        "premium_notes": prem_notes, "lot_size_note": "current lot size from today's instrument master applied to all periods",
        "from": frm.isoformat(), "to": to.isoformat()}, split)


def run_and_store(rt, index, c5, premiums, lot, provenance, split=(0.6, 0.2, 0.2)) -> dict:
    if not c5:
        raise RuntimeError("No genuine candle data returned for the period")
    days = sorted({c.ts.date() for c in c5})
    n = len(days)
    a, b = int(n * split[0]), int(n * (split[0] + split[1]))
    periods = {"development": (days[0], days[max(a - 1, 0)]), "validation": (days[min(a, n - 1)], days[max(b - 1, 0)]),
               "out_of_sample": (days[min(b, n - 1)], days[-1])}
    res = run_backtest(index, c5, rt.svc.p, premiums, lot, rt.svc.schedule)
    trades = res["trades"]
    kind = "technical_with_premium_overlay" if premiums else "technical_only"
    value_key = "net" if premiums and all(t.net is not None for t in trades) else "r_multiple"
    rows = [{**t.to_dict(), "opened_at": t.opened_at, "gross": t.gross, "charges": t.charges or 0,
             "net": t.net if t.net is not None else t.r_multiple} for t in trades]
    seg = {}
    for name, (s, e) in periods.items():
        sub = [r for r in rows if s <= r["opened_at"].date() <= e]
        seg[name] = {"from": s.isoformat(), "to": e.isoformat(), "metrics": trade_metrics(sub, "net")}
    results = {
        "kind": kind, "unit": "rupees net of costs (1 lot)" if value_key == "net" else "R-multiples (underlying)",
        "disclaimer": ("TECHNICAL-ONLY TEST: option-chain/OI/PCR/Max Pain/Greeks filters and the 75-point score gate were NOT "
                       "applied because genuine historical option-chain data is unavailable. This is not a full strategy backtest "
                       "and must not be read as evidence of profitability."),
        "overall": trade_metrics(rows, "net"), "periods": seg, "technical_candidates": res["technical_candidates"],
        "rejected_reasons": res["rejected_reasons"], "trades": [ {k: v for k, v in r.items() if k != "opened_at"} | {"opened_at": r["opened_at"].isoformat()} for r in rows],
        "data_coverage": {"days": n, "first": days[0].isoformat(), "last": days[-1].isoformat(), "bars": len(c5)},
        "no_optimisation": "Parameters fixed at current settings; no tuning on any period.",
    }
    rid = uuid.uuid4().hex[:12]
    cfg = {"index": index, "params": rt.svc.p.to_dict(), "strategy_version": STRATEGY_VERSION, "split": list(split),
           "charges": rt.svc.schedule.to_dict(), "lot_size": lot}
    prov = {**provenance, "data_sha256": hashlib.sha256(json.dumps([c.to_dict() for c in c5]).encode()).hexdigest()}
    with rt.Session() as s:
        s.add(dbm.BacktestRun(id=rid, kind=kind, config=cfg, provenance=prov, results=results))
        s.commit()
    return {"id": rid, "kind": kind, "config": cfg, "provenance": prov, "results": results}
