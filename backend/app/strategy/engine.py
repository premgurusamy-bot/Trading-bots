"""Deterministic decision engine. Final authority for combined technical + derivatives + Greeks
decisions.

`evaluate()` is a pure function of its inputs (candles, chain snapshots, params, risk gate, time).
The full input snapshot is returned so every decision can be stored and reproduced.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Sequence

from app.core.types import Candle, DataQuality, OptionChainSnapshot, OptionType
from app.strategy import indicators as ind
from app.strategy.levels import build_levels, Level
from app.strategy.optionchain import analyse_chain, derivatives_confirms, window_strikes
from app.strategy.params import StrategyParams, STRATEGY_VERSION
from app.strategy.patterns import detect_at, recent_formations, BULLISH_REJECTION, BEARISH_REJECTION
from app.strategy.scoring import ScoreInputs, score
from app.strategy.selection import select_contract
from app.strategy.setups import reaction_zone, find_breakout_retest, build_plan, premium_plan
from app.strategy.signals import Signal, signal_id, expiry_time
from app.strategy.structure import confirmed_pivots, evaluate_trend, _last_completed_15m_index

HISTORY_BARS_5M = 300     # fixed warm-up window (same in live and backtest => identical indicator values)
HISTORY_BARS_15M = 120


def params_hash(p: StrategyParams) -> str:
    return hashlib.sha256(json.dumps(p.to_dict(), sort_keys=True).encode()).hexdigest()[:16]


def merge_futures_volume(spot: Sequence[Candle], fut: Sequence[Candle]) -> list[Candle]:
    """Index spot has no traded volume. Attach current-month futures volume by identical candle timestamp.
    Where futures volume is missing the candle keeps volume 0 (never fabricated)."""
    vmap = {c.ts: c.volume for c in fut}
    out = []
    for c in spot:
        out.append(Candle(ts=c.ts, open=c.open, high=c.high, low=c.low, close=c.close,
                          volume=vmap.get(c.ts, 0.0), complete=c.complete, source=c.source + "+fut_vol", oi=c.oi))
    return out


@dataclass
class MarketContext:
    index: str
    c5: list[Candle]                       # session candles incl. history; volume = futures volume
    c15: list[Candle]
    chain: Optional[OptionChainSnapshot]
    prev_chain: Optional[OptionChainSnapshot]
    daily_prev: Optional[Candle]
    now: datetime
    quality: DataQuality
    risk_gate: dict                        # {"ok": bool, "reasons": [...]}
    underlying_prev_close: Optional[float] = None
    fut_price_change: Optional[float] = None
    fut_oi_change: Optional[float] = None


@dataclass
class EvalResult:
    index: str
    candle_ts: Optional[datetime]
    state: str                             # "NO_TRADE" | "SIDEWAYS" | "BULLISH" | "BEARISH" | "BLOCKED"
    trend: Optional[dict]
    levels: list[dict] = field(default_factory=list)
    chain_analysis: Optional[dict] = None
    candidate: Optional[dict] = None       # technical candidate (mandatory results, score)
    signal: Optional[Signal] = None
    rejected: Optional[dict] = None        # research-log record
    snapshot: dict = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "index": self.index, "candle_ts": self.candle_ts.isoformat() if self.candle_ts else None,
            "state": self.state, "trend": self.trend, "levels": self.levels, "chain_analysis": self.chain_analysis,
            "candidate": self.candidate, "signal": self.signal.to_dict() if self.signal else None,
            "rejected": self.rejected, "messages": self.messages,
        }


def _cache(c5, c15, p: StrategyParams) -> dict:
    closes5 = [c.close for c in c5]
    closes15 = [c.close for c in c15]
    return {
        "e9_5": ind.ema(closes5, p.ema_fast), "e20_5": ind.ema(closes5, p.ema_slow),
        "atr5": ind.atr(c5, p.atr_len), "adx5": ind.adx(c5, p.adx_len),
        "piv5": confirmed_pivots(c5, p.pivot_len), "e9_15": ind.ema(closes15, p.ema_fast),
        "e20_15": ind.ema(closes15, p.ema_slow), "piv15": confirmed_pivots(c15, p.pivot_len),
        "vwap5": ind.session_vwap(c5), "volsma5": ind.sma([c.volume if c.volume > 0 else None for c in c5], p.volume_sma_len),
        "rsi5": ind.rsi(closes5, p.rsi_len),
    }


def snapshot_hash(snap: dict) -> str:
    return hashlib.sha256(json.dumps(snap, sort_keys=True, default=str).encode()).hexdigest()[:20]


def evaluate(ctx: MarketContext, p: StrategyParams) -> EvalResult:
    c5_all = [c for c in ctx.c5 if c.complete]
    c5 = c5_all[-HISTORY_BARS_5M:]
    c15 = [c for c in ctx.c15 if c.complete][-HISTORY_BARS_15M:]
    if len(c5) < max(p.ema_slow, p.atr_len, 2 * p.adx_len) + 2:
        return EvalResult(ctx.index, c5[-1].ts if c5 else None, "NO_TRADE", None,
                          messages=["Insufficient completed 5m history for indicators"])
    i = len(c5) - 1
    conf = c5[i]
    cache = _cache(c5, c15, p)
    trend = evaluate_trend(c5, c15, i, cache["vwap5"], p, cache)
    close_time = conf.ts + timedelta(minutes=5)
    j15 = _last_completed_15m_index(c15, close_time)
    levels: list[Level] = build_levels(c5, c15, i, j15, cache, p, ctx.chain, ctx.daily_prev)
    atr = cache["atr5"][i]
    an = None
    if ctx.chain is not None:
        an = analyse_chain(ctx.chain, ctx.prev_chain, p.near_atm_pcr_strikes, ctx.underlying_prev_close,
                           ctx.fut_price_change, ctx.fut_oi_change)

    snapshot = {
        "strategy_version": STRATEGY_VERSION, "params": p.to_dict(), "params_hash": params_hash(p),
        "index": ctx.index, "now": ctx.now.isoformat(), "candle_ts": conf.ts.isoformat(),
        "c5_tail": [c.to_dict() for c in c5[-60:]], "c15_tail": [c.to_dict() for c in c15[-30:]],
        "chain": ctx.chain.to_dict() if ctx.chain else None,
        "prev_chain_ts": ctx.prev_chain.received_at.isoformat() if ctx.prev_chain else None,
        "daily_prev": ctx.daily_prev.to_dict() if ctx.daily_prev else None,
        "quality": {"ok": ctx.quality.ok, "reasons": ctx.quality.reasons}, "risk_gate": ctx.risk_gate,
    }
    res = EvalResult(ctx.index, conf.ts, "NO_TRADE", trend.to_dict(), [l.to_dict() for l in levels],
                     an.to_dict() if an else None, snapshot=snapshot)
    if trend.sideways:
        res.state = "SIDEWAYS"
    elif trend.direction:
        res.state = trend.direction
    D = trend.direction
    hits = detect_at(c5, i, cache["piv5"], atr)
    if D is None:
        res.messages.append("No directional setup: " + ("sideways filter active" if trend.sideways else "trend conditions not all met"))
        return res

    rej_set = BULLISH_REJECTION if D == "BULLISH" else BEARISH_REJECTION
    rej_hits = [h for h in hits if h.name in rej_set]
    zone = reaction_zone(c5, i, levels, atr, D, p)
    levels_at = lambda k: build_levels(c5, c15, k, _last_completed_15m_index(c15, c5[k].ts + timedelta(minutes=5)),
                                       cache, p, ctx.chain, ctx.daily_prev)
    brt = find_breakout_retest(c5, i, levels_at, cache["volsma5"], atr, D, p)
    setup = None
    if zone is not None:
        setup = "A" if D == "BULLISH" else "B"
    elif brt is not None:
        setup = "C"
        bl = brt.level
        from app.strategy.levels import confluence_at
        kinds = ("support", "both", "resistance")
        zone = confluence_at(levels, bl["price"], p.zone_atr_frac * atr, kinds)

    if not rej_hits and setup is None:
        res.messages.append(f"{D} trend, but no reaction at a level and no confirmation pattern on this candle")
        return res

    plan = build_plan(conf, atr, cache["e9_5"][i], levels, D, p)
    sel = None
    if ctx.chain is not None:
        sel = select_contract(ctx.chain, D, p, ctx.now, ctx.now.date())
    opt = sel.selected.quote if (sel and sel.selected) else None
    prem = premium_plan(opt, plan.risk_points, p) if opt else None
    ot = OptionType.CE if D == "BULLISH" else OptionType.PE
    gam_max = None
    if ctx.chain is not None:
        gs = [q.gamma for k in window_strikes(ctx.chain, p.near_atm_pcr_strikes)
              for q in [ctx.chain.get(k, ot)] if q is not None and q.gamma is not None]
        gam_max = max(gs) if gs else None
    vol = conf.volume if conf.volume > 0 else None
    vsma = cache["volsma5"][i]
    forms = recent_formations(c5, i, cache["piv5"], cache["atr5"])
    si = ScoreInputs(
        direction=D, ema9_15=trend.values["ema9_15"], ema20_15=trend.values["ema20_15"],
        ema9_5=cache["e9_5"][i], ema20_5=cache["e20_5"][i], close=conf.close, vwap=cache["vwap5"][i], atr=atr,
        structure15=trend.values["structure15"], zone_confluence=zone, chain=an, option=opt,
        near_atm_gamma_max=gam_max, iv_median=sel.near_atm_iv_median if sel else None, rejection_hits=rej_hits,
        confirm_candle=conf, volume=vol, volume_sma=vsma, formations=forms, risk_points=plan.risk_points,
        spot=ctx.chain.underlying_ltp if ctx.chain else conf.close,
    )
    sc = score(si, p)
    bull = D == "BULLISH"
    tb = trend.bullish if bull else trend.bearish
    vol_ok = vol is not None and vsma is not None and vol >= p.volume_confirm_mult * vsma
    if setup == "C":
        vol_ok = vol_ok and brt is not None
    in_window = ctx.now.astimezone(conf.ts.tzinfo).time() <= p.entry_cutoff
    mandatory = {
        "data_quality": ctx.quality.ok,
        "risk_gate": bool(ctx.risk_gate.get("ok")),
        "before_entry_cutoff": in_window,
        "trend_15m": list(tb.values())[0],
        "ema_5m_alignment": list(tb.values())[1],
        "vwap_side": list(tb.values())[2],
        "ema9_slope": list(tb.values())[3],
        "no_opposing_structure_break": list(tb.values())[4],
        "not_sideways": not trend.sideways,
        "setup_location": setup is not None,
        "derivatives_confirmation": an is not None and derivatives_confirms(an, D),
        "option_quality": sel is not None and sel.ok,
        "rejection_candle_confirmed": bool(rej_hits) and conf.complete,
        "volume_confirmation": vol_ok,
        "min_score": sc.total >= p.min_score,
        "not_chasing": plan.not_chasing,
        "reward_risk_to_next_sr": plan.rr_ok,
        "premium_plan_available": prem is not None,
    }
    failed = [k for k, v in mandatory.items() if not v]
    cand = {
        "direction": D, "setup": setup, "plan": plan.to_dict(), "score": sc.to_dict(), "mandatory": mandatory,
        "failed": failed, "zone": zone.to_dict() if zone else None,
        "breakout_retest": brt.to_dict() if brt else None, "selection": sel.to_dict() if sel else None,
        "premium": prem, "patterns": [h.to_dict() for h in hits], "formations": [h.to_dict() for h in forms],
        "rsi_info_only": cache["rsi5"][i],
    }
    res.candidate = cand
    snap_ref = snapshot_hash(snapshot)
    snapshot["ref"] = snap_ref
    if failed:
        res.rejected = {"id": signal_id(ctx.index, setup or "-", D, conf.ts), "at": ctx.now.isoformat(),
                        "reasons": failed, "score": sc.total, "candidate": cand, "snapshot_ref": snap_ref}
        res.messages.append(f"Candidate rejected: {failed}")
        if ctx.quality.reasons:
            res.messages.append("Data faults: " + "; ".join(ctx.quality.reasons))
        return res
    sig = Signal(
        id=signal_id(ctx.index, setup, D, conf.ts), index=ctx.index, direction=D, setup=setup,
        confirm_ts=conf.ts, created_at=ctx.now, expires_at=expiry_time(conf.ts, p.signal_validity_candles),
        plan=plan.to_dict(), premium=prem, option=opt, score=sc.to_dict(), mandatory=mandatory,
        invalidation=[
            f"Completed 5m close {'below' if bull else 'above'} stop {plan.stop}",
            "Confirmed opposing market-structure break on 5m",
            f"15m EMA9 crosses {'below' if bull else 'above'} EMA20",
            "Derivatives confirmation lost on latest option chain",
            f"No trigger by {expiry_time(conf.ts, p.signal_validity_candles).strftime('%H:%M')} (3 completed 5m candles)",
        ],
        strategy_version=STRATEGY_VERSION, params_hash=params_hash(p), snapshot_ref=snap_ref,
    )
    sig.history.append({"from": None, "to": "PENDING", "at": ctx.now.isoformat(), "reason": f"Setup {setup} score {sc.total}"})
    res.signal = sig
    res.messages.append(f"Signal PENDING: setup {setup} {D} score {sc.total} ({sc.band})")
    return res


def still_valid(sig: Signal, ctx: MarketContext, p: StrategyParams) -> tuple[bool, str]:
    """Pending-signal invalidation check after each new completed 5m candle (see Signal.invalidation)."""
    c5 = [c for c in ctx.c5 if c.complete][-HISTORY_BARS_5M:]
    c15 = [c for c in ctx.c15 if c.complete][-HISTORY_BARS_15M:]
    if not c5:
        return False, "no data"
    i = len(c5) - 1
    bull = sig.direction == "BULLISH"
    last = c5[i]
    if (bull and last.close < sig.plan["stop"]) or (not bull and last.close > sig.plan["stop"]):
        return False, f"close {last.close} beyond stop {sig.plan['stop']}"
    cache = _cache(c5, c15, p)
    tr = evaluate_trend(c5, c15, i, cache["vwap5"], p, cache)
    tb = tr.bullish if bull else tr.bearish
    if not list(tb.values())[4]:
        return False, "opposing structure break"
    if not list(tb.values())[0]:
        return False, "15m EMA trend flipped"
    if ctx.chain is not None:
        an = analyse_chain(ctx.chain, ctx.prev_chain, p.near_atm_pcr_strikes, ctx.underlying_prev_close,
                           ctx.fut_price_change, ctx.fut_oi_change)
        if not derivatives_confirms(an, sig.direction):
            return False, "derivatives confirmation lost"
    if not ctx.quality.ok:
        return False, "data quality fault: " + "; ".join(ctx.quality.reasons)
    return True, ""
