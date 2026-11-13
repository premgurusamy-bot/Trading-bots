"""Confirmed pivots, market structure, trend and sideways filters.

Pivot rule (no repainting): bar p is a pivot high if high[p] > high of each of the `n` bars before
it and >= high of each of the `n` bars after it (mirror for lows). The pivot only becomes
known at bar p+n (its `confirm_index`). A function evaluated at bar i may only use pivots with
confirm_index <= i. A pivot's price and position never change after confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Sequence

from app.core.types import Candle
from app.strategy import indicators as ind
from app.strategy.params import StrategyParams


@dataclass(frozen=True)
class Pivot:
    kind: str           # "H" or "L"
    index: int
    ts: datetime
    price: float
    confirm_index: int
    confirm_ts: datetime

    def to_dict(self):
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        d["confirm_ts"] = self.confirm_ts.isoformat()
        return d


def confirmed_pivots(candles: Sequence[Candle], n: int, upto: Optional[int] = None) -> list[Pivot]:
    if upto is None:
        upto = len(candles) - 1
    out: list[Pivot] = []
    for p in range(n, upto - n + 1):
        c = candles[p]
        left = candles[p - n:p]
        right = candles[p + 1:p + n + 1]
        if all(c.high > x.high for x in left) and all(c.high >= x.high for x in right):
            out.append(Pivot("H", p, c.ts, c.high, p + n, candles[p + n].ts))
        if all(c.low < x.low for x in left) and all(c.low <= x.low for x in right):
            out.append(Pivot("L", p, c.ts, c.low, p + n, candles[p + n].ts))
    out.sort(key=lambda x: (x.confirm_index, x.index))
    return out


@dataclass
class StructureState:
    trend: str                         # "up" (HH+HL), "down" (LH+LL), "none"
    higher_high: Optional[bool]
    higher_low: Optional[bool]
    bearish_breakdown: bool            # completed close below last confirmed swing low
    bullish_breakout: bool             # completed close above last confirmed swing high
    last_high: Optional[Pivot]
    last_low: Optional[Pivot]
    prev_high: Optional[Pivot]
    prev_low: Optional[Pivot]

    def to_dict(self):
        return {
            "trend": self.trend, "higher_high": self.higher_high, "higher_low": self.higher_low,
            "bearish_breakdown": self.bearish_breakdown, "bullish_breakout": self.bullish_breakout,
            "last_high": self.last_high.to_dict() if self.last_high else None,
            "last_low": self.last_low.to_dict() if self.last_low else None,
            "prev_high": self.prev_high.to_dict() if self.prev_high else None,
            "prev_low": self.prev_low.to_dict() if self.prev_low else None,
        }


def structure_state(candles: Sequence[Candle], pivots: Sequence[Pivot], i: int) -> StructureState:
    known = [p for p in pivots if p.confirm_index <= i]
    highs = [p for p in known if p.kind == "H"]
    lows = [p for p in known if p.kind == "L"]
    lh = highs[-1] if highs else None
    ph = highs[-2] if len(highs) >= 2 else None
    ll = lows[-1] if lows else None
    pl = lows[-2] if len(lows) >= 2 else None
    hh = (lh.price > ph.price) if (lh and ph) else None
    hl = (ll.price > pl.price) if (ll and pl) else None
    if hh is True and hl is True:
        trend = "up"
    elif hh is False and hl is False:
        trend = "down"
    else:
        trend = "none"
    breakdown = False
    if ll is not None:
        breakdown = any(candles[k].close < ll.price for k in range(ll.confirm_index, i + 1) if candles[k].complete)
    breakout = False
    if lh is not None:
        breakout = any(candles[k].close > lh.price for k in range(lh.confirm_index, i + 1) if candles[k].complete)
    return StructureState(trend, hh, hl, breakdown, breakout, lh, ll, ph, pl)


@dataclass
class TrendEval:
    direction: Optional[str]           # "BULLISH" | "BEARISH" | None
    sideways: bool
    bullish: dict
    bearish: dict
    sideways_checks: dict
    values: dict

    def to_dict(self):
        return asdict(self)


def _last_completed_15m_index(c15: Sequence[Candle], at: datetime) -> Optional[int]:
    """Index of the last 15m candle that is complete and whose end <= `at` (the 5m candle close)."""
    from datetime import timedelta
    idx = None
    for k, c in enumerate(c15):
        if c.complete and c.ts + timedelta(minutes=15) <= at:
            idx = k
    return idx


def evaluate_trend(c5: Sequence[Candle], c15: Sequence[Candle], i: int, vwap5: ind.Series,
                   p: StrategyParams, cache: Optional[dict] = None) -> TrendEval:
    """Evaluate trend/sideways filters at completed 5m candle i. 15m inputs are restricted to 15m
    candles fully completed by the close of 5m candle i (no look-ahead)."""
    from datetime import timedelta
    cache = cache if cache is not None else {}
    closes5 = [c.close for c in c5]
    e9 = cache.get("e9_5") or ind.ema(closes5, p.ema_fast)
    e20 = cache.get("e20_5") or ind.ema(closes5, p.ema_slow)
    atr5 = cache.get("atr5") or ind.atr(c5, p.atr_len)
    adx5, _, _ = cache.get("adx5") or ind.adx(c5, p.adx_len)
    piv5 = cache.get("piv5") or confirmed_pivots(c5, p.pivot_len)
    close_time = c5[i].ts + timedelta(minutes=5)
    j = _last_completed_15m_index(c15, close_time)
    closes15 = [c.close for c in c15]
    e9_15 = cache.get("e9_15") or ind.ema(closes15, p.ema_fast)
    e20_15 = cache.get("e20_15") or ind.ema(closes15, p.ema_slow)
    piv15 = cache.get("piv15") or confirmed_pivots(c15, p.pivot_len)

    v = {
        "close": c5[i].close, "ema9": e9[i], "ema20": e20[i], "vwap": vwap5[i], "atr": atr5[i],
        "adx": adx5[i], "ema9_15": e9_15[j] if j is not None else None,
        "ema20_15": e20_15[j] if j is not None else None, "idx15": j,
    }
    sl = ind.slope(e9, i, p.ema_slope_lookback)
    v["ema9_slope"] = sl
    flat_thr = (p.flat_slope_atr_frac * atr5[i]) if atr5[i] else None
    v["flat_threshold"] = flat_thr
    st5 = structure_state(c5, piv5, i)
    st15 = structure_state(c15, piv15, j) if j is not None else None
    v["structure5"] = st5.to_dict()
    v["structure15"] = st15.to_dict() if st15 else None

    def ok(x):
        return x is not None

    have15 = ok(v["ema9_15"]) and ok(v["ema20_15"])
    have5 = ok(e9[i]) and ok(e20[i])
    bull = {
        "ema15_9_above_20": have15 and v["ema9_15"] > v["ema20_15"],
        "ema5_9_above_20": have5 and e9[i] > e20[i],
        "price_above_vwap": ok(vwap5[i]) and c5[i].close > vwap5[i],
        "ema9_slope_positive": ok(sl) and ok(flat_thr) and sl > flat_thr,
        "no_bearish_breakdown": not st5.bearish_breakdown,
    }
    bear = {
        "ema15_9_below_20": have15 and v["ema9_15"] < v["ema20_15"],
        "ema5_9_below_20": have5 and e9[i] < e20[i],
        "price_below_vwap": ok(vwap5[i]) and c5[i].close < vwap5[i],
        "ema9_slope_negative": ok(sl) and ok(flat_thr) and sl < -flat_thr,
        "no_bullish_breakout": not st5.bullish_breakout,
    }
    start = i - p.sideways_lookback + 1
    ema_x = ind.crosses(e9, e20, start, i)
    vwap_x = ind.crosses([c.close for c in c5], vwap5, start, i)
    side = {
        "ema_crosses": ema_x,
        "ema_crosses_excess": ema_x > p.max_ema_crosses,
        "vwap_crosses": vwap_x,
        "vwap_crosses_excess": vwap_x > p.max_vwap_crosses,
        "flat_ema_slope": (not ok(sl)) or (not ok(flat_thr)) or abs(sl) <= flat_thr,
        "no_structure": st5.trend == "none" and (st15 is None or st15.trend == "none"),
        "adx_below_min": (not ok(adx5[i])) or adx5[i] < p.adx_min_trend,
    }
    sideways = (side["ema_crosses_excess"] or side["vwap_crosses_excess"] or side["adx_below_min"]
                or side["flat_ema_slope"] or side["no_structure"])
    direction = None
    if not sideways:
        if all(bull.values()):
            direction = "BULLISH"
        elif all(bear.values()):
            direction = "BEARISH"
    return TrendEval(direction, sideways, bull, bear, side, v)
