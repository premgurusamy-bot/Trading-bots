"""Setup detection (A/B pullback-rejection, C breakout-retest) and trade-plan maths.

Reaching a level (documented rule): the confirmation candle (or the candle before it) trades into
the level, i.e. its low (bull) / high (bear) comes within `touch_atr_frac * ATR` of the level or
pierces it, and the confirmation candle CLOSES back on the trade side of the level.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, Sequence

from app.core.types import Candle
from app.strategy.levels import Level, Confluence, confluence_at, next_level_beyond
from app.strategy.params import StrategyParams

IMPORTANT_FAMILIES = {"prev_day", "opening_range", "swing", "zone", "oi", "trendline"}


def reaction_zone(c5: Sequence[Candle], i: int, levels: Sequence[Level], atr: float, direction: str,
                  p: StrategyParams) -> Optional[Confluence]:
    """Levels the pullback reached (support for bull, resistance for bear), grouped into one confluence."""
    tol = p.touch_atr_frac * atr
    conf = c5[i]
    probe_candles = [c5[i]] + ([c5[i - 1]] if i >= 1 else [])
    hit: list[Level] = []
    for l in levels:
        if direction == "BULLISH" and l.kind in ("support", "both"):
            reached = any(c.low <= l.high + tol for c in probe_candles)
            held = conf.close > l.low
            # a level far below the probe (> tol under the low of the deepest candle) was not reached either
            deep = min(c.low for c in probe_candles)
            if reached and held and l.high >= deep - tol:
                hit.append(l)
        if direction == "BEARISH" and l.kind in ("resistance", "both"):
            reached = any(c.high >= l.low - tol for c in probe_candles)
            held = conf.close < l.high
            top = max(c.high for c in probe_candles)
            if reached and held and l.low <= top + tol:
                hit.append(l)
    if not hit:
        return None
    # cluster around the level closest to the probe extreme
    ref = min(c.low for c in probe_candles) if direction == "BULLISH" else max(c.high for c in probe_candles)
    anchor = min(hit, key=lambda l: l.distance(ref))
    zone_tol = p.zone_atr_frac * atr
    kinds = ("support", "both") if direction == "BULLISH" else ("resistance", "both")
    return confluence_at(hit, anchor.price, zone_tol + (anchor.high - anchor.low) / 2, kinds)


@dataclass
class BreakoutRetest:
    level: dict
    breakout_index: int
    breakout_volume: float
    volume_sma: float
    retest_index: int

    def to_dict(self):
        return asdict(self)


def find_breakout_retest(c5: Sequence[Candle], i: int, levels_at: callable, vol_sma: Sequence[Optional[float]],
                         atr: float, direction: str, p: StrategyParams) -> Optional[BreakoutRetest]:
    """Setup C. A completed candle b in [i-retest_max_candles, i-1] closed through an important level that
    existed at b-1 (levels_at(b-1)), the prior candle closed on the other side, b's volume >= breakout_volume_mult
    x SMA20, then some candle in (b, i] retested the level within touch tolerance while closes stayed on the
    breakout side, and candle i is the confirmation."""
    tol = p.touch_atr_frac * atr
    for b in range(i - 1, max(i - p.retest_max_candles, 1) - 1, -1):
        cb, cp = c5[b], c5[b - 1]
        vs = vol_sma[b]
        if vs is None or vs <= 0 or cb.volume < p.breakout_volume_mult * vs:
            continue
        for l in levels_at(b - 1):
            if l.family not in IMPORTANT_FAMILIES or l.strength < 1.0:
                continue
            if direction == "BULLISH" and l.kind in ("resistance", "both"):
                if not (cp.close <= l.high < cb.close):
                    continue
                after = c5[b + 1:i + 1]
                if any(c.close <= l.low for c in after):
                    continue  # failed breakout
                rt = [k for k in range(b + 1, i + 1) if c5[k].low <= l.high + tol]
                if rt:
                    return BreakoutRetest(l.to_dict(), b, cb.volume, vs, rt[-1])
            if direction == "BEARISH" and l.kind in ("support", "both"):
                if not (cp.close >= l.low > cb.close):
                    continue
                after = c5[b + 1:i + 1]
                if any(c.close >= l.high for c in after):
                    continue
                rt = [k for k in range(b + 1, i + 1) if c5[k].high >= l.low - tol]
                if rt:
                    return BreakoutRetest(l.to_dict(), b, cb.volume, vs, rt[-1])
    return None


@dataclass
class TradePlan:
    direction: str
    trigger: float
    stop: float
    risk_points: float
    t1: float
    t2: float
    t3: Optional[float]
    next_sr: Optional[dict]
    rr_to_next_sr: Optional[float]
    rr_ok: bool
    not_chasing: bool
    chase_detail: dict

    def to_dict(self):
        return asdict(self)


def build_plan(conf: Candle, atr: float, ema9: Optional[float], levels: Sequence[Level], direction: str,
               p: StrategyParams) -> TradePlan:
    tick = p.tick_size
    if direction == "BULLISH":
        trigger = conf.high + tick
        stop = conf.low - p.sl_atr_buffer * atr
        r = trigger - stop
        t1, t2, t3c = trigger + p.t1_r * r, trigger + p.t2_r * r, trigger + p.t3_r * r
        nxt = next_level_beyond(levels, trigger, "BULLISH")
        room = (nxt.low - trigger) if nxt else None
        t3 = t3c if (nxt is None or nxt.low >= t3c) else None
        ext = (trigger - ema9) if ema9 is not None else None
    else:
        trigger = conf.low - tick
        stop = conf.high + p.sl_atr_buffer * atr
        r = stop - trigger
        t1, t2, t3c = trigger - p.t1_r * r, trigger - p.t2_r * r, trigger - p.t3_r * r
        nxt = next_level_beyond(levels, trigger, "BEARISH")
        room = (trigger - nxt.high) if nxt else None
        t3 = t3c if (nxt is None or nxt.high <= t3c) else None
        ext = (ema9 - trigger) if ema9 is not None else None
    rr = (room / r) if (room is not None and r > 0) else None
    rr_ok = r > 0 and (rr is None or rr >= p.min_rr_to_next_sr)
    chase = {
        "candle_range": conf.range, "max_range": p.max_entry_candle_atr * atr,
        "trigger_ext_from_ema9": ext, "max_ext": p.max_entry_ext_atr * atr,
    }
    not_chasing = conf.range <= p.max_entry_candle_atr * atr and (ext is None or ext <= p.max_entry_ext_atr * atr)
    return TradePlan(direction, round(trigger, 2), round(stop, 2), round(r, 2), round(t1, 2), round(t2, 2),
                     round(t3, 2) if t3 is not None else None, nxt.to_dict() if nxt else None,
                     round(rr, 2) if rr is not None else None, rr_ok, not_chasing, chase)


def premium_plan(option: dict, risk_points: float, p: StrategyParams, iv_shock_pts: float = 1.0) -> Optional[dict]:
    """Option premium stop — NOT a linear translation of index points.

    Estimated premium loss if the underlying moves 1R against us:
        loss = |delta|*R - 0.5*gamma*R^2 (second-order Taylor; gamma reduces a long option's loss)
             + vega*iv_shock_pts           (adverse IV move of `iv_shock_pts` vol points)
             + |theta| * (15/375)           (decay over ~3 candles of a 375-minute session-day)
    Premium SL = entry_premium - loss - (spread + slippage_ticks*tick) as exit-execution buffer.
    Entry premium assumed at the ASK (buyer pays the offer), plus slippage.
    """
    d, g, th = option.get("delta"), option.get("gamma"), option.get("theta")
    ask, bid = option.get("ask"), option.get("bid")
    vega = option.get("vega") or 0.0
    if d is None or g is None or th is None or ask is None or bid is None or ask <= 0:
        return None
    R = risk_points
    loss = abs(d) * R - 0.5 * g * R * R + abs(vega) * iv_shock_pts + abs(th) * (15 / 375)
    loss = max(loss, abs(d) * R * 0.5)  # floor: never assume gamma removes more than half the delta loss
    slip = p.slippage_ticks * p.tick_size
    entry = ask + slip
    exec_buf = (ask - bid) + slip
    sl = entry - loss - exec_buf
    sl = max(round(sl / p.tick_size) * p.tick_size, p.tick_size)
    return {
        "entry_premium_est": round(entry, 2), "premium_sl": round(sl, 2),
        "premium_risk_per_unit": round(entry - sl, 2), "model": "taylor2+vega+theta",
        "components": {"delta_loss": abs(d) * R, "gamma_relief": 0.5 * g * R * R, "vega_shock": abs(vega) * iv_shock_pts,
                       "theta_decay": abs(th) * (15 / 375), "exec_buffer": exec_buf},
        "controls_exit": ("Exit on whichever triggers first: underlying LTP crosses the technical SL, or the option "
                          "BID falls to the premium SL. The premium SL caps rupee loss under non-linear option moves; "
                          "the technical SL defines setup invalidation. Secure-Exit rupee rules also apply."),
    }
