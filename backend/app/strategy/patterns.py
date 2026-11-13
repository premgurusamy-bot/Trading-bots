"""Candle and price-action pattern recognition (completed candles only).

All thresholds are explicit module constants; definitions are reproduced in docs/PATTERNS.md.
A pattern alone never generates a trade.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, Sequence

from app.core.types import Candle
from app.strategy.structure import Pivot

# ---- thresholds (fractions of candle range unless stated) ----
DOJI_BODY_MAX = 0.10            # body <= 10% of range
HAMMER_LOWER_WICK_BODY = 2.0    # lower wick >= 2x body
HAMMER_UPPER_WICK_MAX = 0.25    # upper wick <= 25% of range
HAMMER_CLOSE_LOC_MIN = 0.60     # close in top 40% of range
PIN_WICK_MIN = 0.66             # rejecting wick >= 2/3 of range
PIN_BODY_MAX = 0.30
MS_FIRST_BODY_MIN = 0.50        # morning/evening star first candle body >= 50% of its range
MS_STAR_BODY_MAX = 0.30         # star body <= 30% of first candle body
SOLDIER_BODY_MIN = 0.50
SOLDIER_WICK_MAX = 0.30
FLAG_POLE_ATR = 2.0             # pole move >= 2 ATR within <= 4 bars
FLAG_MAX_RETRACE = 0.50
FLAG_MIN_BARS, FLAG_MAX_BARS = 2, 6
EQUAL_LEVEL_ATR = 0.25          # tops/bottoms "equal" within 0.25 ATR
FORMATION_LOOKBACK = 6          # multi-bar formations must have confirmed within last 6 bars

BULLISH_REJECTION = {"bullish_engulfing", "hammer", "bullish_pin_bar", "morning_star"}
BEARISH_REJECTION = {"bearish_engulfing", "shooting_star", "bearish_pin_bar", "evening_star"}
BULLISH_FORMATIONS = {"three_white_soldiers", "bull_flag", "inside_bar_bullish_breakout",
                      "double_bottom", "ascending_triangle", "inverse_head_and_shoulders"}
BEARISH_FORMATIONS = {"three_black_crows", "bear_flag", "inside_bar_bearish_breakdown",
                      "double_top", "descending_triangle", "head_and_shoulders"}


@dataclass
class PatternHit:
    name: str
    direction: str        # "BULLISH" | "BEARISH" | "NEUTRAL"
    end_index: int
    bars: int
    details: dict

    def to_dict(self):
        return asdict(self)


def close_location(c: Candle) -> float:
    return 0.5 if c.range == 0 else (c.close - c.low) / c.range


def is_doji(c: Candle) -> bool:
    return c.range > 0 and c.body <= DOJI_BODY_MAX * c.range


def is_hammer(c: Candle) -> bool:
    return (c.range > 0 and c.lower_wick >= HAMMER_LOWER_WICK_BODY * max(c.body, 0.05 * c.range)
            and c.upper_wick <= HAMMER_UPPER_WICK_MAX * c.range and close_location(c) >= HAMMER_CLOSE_LOC_MIN)


def is_shooting_star(c: Candle) -> bool:
    return (c.range > 0 and c.upper_wick >= HAMMER_LOWER_WICK_BODY * max(c.body, 0.05 * c.range)
            and c.lower_wick <= HAMMER_UPPER_WICK_MAX * c.range and close_location(c) <= 1 - HAMMER_CLOSE_LOC_MIN)


def is_bull_pin(c: Candle) -> bool:
    return c.range > 0 and c.lower_wick >= PIN_WICK_MIN * c.range and c.body <= PIN_BODY_MAX * c.range


def is_bear_pin(c: Candle) -> bool:
    return c.range > 0 and c.upper_wick >= PIN_WICK_MIN * c.range and c.body <= PIN_BODY_MAX * c.range


def is_bull_engulfing(p: Candle, c: Candle) -> bool:
    return p.bearish and c.bullish and c.open <= p.close and c.close >= p.open and c.body > p.body


def is_bear_engulfing(p: Candle, c: Candle) -> bool:
    return p.bullish and c.bearish and c.open >= p.close and c.close <= p.open and c.body > p.body


def is_morning_star(a: Candle, b: Candle, c: Candle) -> bool:
    return (a.bearish and a.range > 0 and a.body >= MS_FIRST_BODY_MIN * a.range and b.body <= MS_STAR_BODY_MAX * a.body
            and max(b.open, b.close) <= a.close + 0.5 * a.body and c.bullish and c.close > (a.open + a.close) / 2)


def is_evening_star(a: Candle, b: Candle, c: Candle) -> bool:
    return (a.bullish and a.range > 0 and a.body >= MS_FIRST_BODY_MIN * a.range and b.body <= MS_STAR_BODY_MAX * a.body
            and min(b.open, b.close) >= a.close - 0.5 * a.body and c.bearish and c.close < (a.open + a.close) / 2)


def _soldier(prev: Candle, c: Candle, bull: bool) -> bool:
    if c.range == 0:
        return False
    if bull:
        return (c.bullish and c.close > prev.close and prev.open <= c.open <= prev.close
                and c.body >= SOLDIER_BODY_MIN * c.range and c.upper_wick <= SOLDIER_WICK_MAX * c.range)
    return (c.bearish and c.close < prev.close and prev.close <= c.open <= prev.open
            and c.body >= SOLDIER_BODY_MIN * c.range and c.lower_wick <= SOLDIER_WICK_MAX * c.range)


def is_three_white_soldiers(a, b, c) -> bool:
    return a.bullish and a.range > 0 and a.body >= SOLDIER_BODY_MIN * a.range and _soldier(a, b, True) and _soldier(b, c, True)


def is_three_black_crows(a, b, c) -> bool:
    return a.bearish and a.range > 0 and a.body >= SOLDIER_BODY_MIN * a.range and _soldier(a, b, False) and _soldier(b, c, False)


def is_inside(mother: Candle, inner: Candle) -> bool:
    return inner.high <= mother.high and inner.low >= mother.low


def is_outside(p: Candle, c: Candle) -> bool:
    return c.high > p.high and c.low < p.low


def detect_flag(cs: Sequence[Candle], i: int, atr: Optional[float], bull: bool) -> Optional[dict]:
    """Pole: net move >= FLAG_POLE_ATR*ATR over <= 4 bars. Flag: 2–6 bars after the pole, retracing
    <= 50% of the pole, with flag highs (bull) not exceeding the pole top. Breakout: candle i closes
    beyond the flag's extreme."""
    if atr is None or atr <= 0:
        return None
    for fb in range(FLAG_MIN_BARS, FLAG_MAX_BARS + 1):
        fs, fe = i - fb, i - 1
        if fs < 1:
            break
        flag = cs[fs:fe + 1]
        for pl in range(1, 5):
            ps = fs - pl
            if ps < 0:
                break
            pole = cs[ps:fs]
            if bull:
                base, top = min(c.low for c in pole), max(c.high for c in pole)
                move = top - base
                if move < FLAG_POLE_ATR * atr or pole[-1].close <= pole[0].open:
                    continue
                if max(c.high for c in flag) > top or min(c.low for c in flag) < top - FLAG_MAX_RETRACE * move:
                    continue
                if cs[i].close > max(c.high for c in flag):
                    return {"pole_bars": pl, "flag_bars": fb, "pole_move": move}
            else:
                top, base = max(c.high for c in pole), min(c.low for c in pole)
                move = top - base
                if move < FLAG_POLE_ATR * atr or pole[-1].close >= pole[0].open:
                    continue
                if min(c.low for c in flag) < base or max(c.high for c in flag) > base + FLAG_MAX_RETRACE * move:
                    continue
                if cs[i].close < min(c.low for c in flag):
                    return {"pole_bars": pl, "flag_bars": fb, "pole_move": move}
    return None


def _pivots_upto(pivots: Sequence[Pivot], i: int, kind: str) -> list[Pivot]:
    return [p for p in pivots if p.confirm_index <= i and p.kind == kind]


def detect_double(cs, pivots, i, atr, bull: bool) -> Optional[dict]:
    """Double bottom: two most recent confirmed swing lows within EQUAL_LEVEL_ATR*ATR with a confirmed swing
    high between them (neckline); confirmed when candle i closes above the neckline, and candle i-1 did not."""
    if atr is None or i < 1:
        return None
    kind, other = ("L", "H") if bull else ("H", "L")
    ps = _pivots_upto(pivots, i, kind)
    if len(ps) < 2:
        return None
    a, b = ps[-2], ps[-1]
    if abs(a.price - b.price) > EQUAL_LEVEL_ATR * atr:
        return None
    mids = [p for p in _pivots_upto(pivots, i, other) if a.index < p.index < b.index]
    if not mids:
        return None
    neck = max(m.price for m in mids) if bull else min(m.price for m in mids)
    if bull and cs[i].close > neck and cs[i - 1].close <= neck:
        return {"first": a.price, "second": b.price, "neckline": neck}
    if not bull and cs[i].close < neck and cs[i - 1].close >= neck:
        return {"first": a.price, "second": b.price, "neckline": neck}
    return None


def detect_triangle(pivots, i, atr, ascending: bool) -> Optional[dict]:
    if atr is None:
        return None
    hs = _pivots_upto(pivots, i, "H")[-2:]
    ls = _pivots_upto(pivots, i, "L")[-2:]
    if len(hs) < 2 or len(ls) < 2:
        return None
    if ascending:
        if abs(hs[0].price - hs[1].price) <= EQUAL_LEVEL_ATR * atr and ls[1].price > ls[0].price:
            return {"flat_top": (hs[0].price + hs[1].price) / 2, "lows": [ls[0].price, ls[1].price]}
    else:
        if abs(ls[0].price - ls[1].price) <= EQUAL_LEVEL_ATR * atr and hs[1].price < hs[0].price:
            return {"flat_bottom": (ls[0].price + ls[1].price) / 2, "highs": [hs[0].price, hs[1].price]}
    return None


def detect_hs(cs, pivots, i, atr, inverse: bool) -> Optional[dict]:
    """Head & shoulders: last three confirmed swing highs with middle highest, shoulders within
    2*EQUAL_LEVEL_ATR*ATR of each other; confirmed when candle i closes below the neckline (lowest swing low
    between shoulders). Inverse mirrors."""
    if atr is None or i < 1:
        return None
    kind, other = ("L", "H") if inverse else ("H", "L")
    ps = _pivots_upto(pivots, i, kind)
    if len(ps) < 3:
        return None
    l, h, r = ps[-3], ps[-2], ps[-1]
    if inverse:
        shape = h.price < l.price and h.price < r.price
    else:
        shape = h.price > l.price and h.price > r.price
    if not shape or abs(l.price - r.price) > 2 * EQUAL_LEVEL_ATR * atr:
        return None
    mids = [p for p in _pivots_upto(pivots, i, other) if l.index < p.index < r.index]
    if not mids:
        return None
    neck = max(m.price for m in mids) if inverse else min(m.price for m in mids)
    if inverse and cs[i].close > neck and cs[i - 1].close <= neck:
        return {"head": h.price, "neckline": neck}
    if not inverse and cs[i].close < neck and cs[i - 1].close >= neck:
        return {"head": h.price, "neckline": neck}
    return None


def detect_at(cs: Sequence[Candle], i: int, pivots: Sequence[Pivot], atr: Optional[float]) -> list[PatternHit]:
    """All patterns whose final candle is i. Requires cs[i] complete."""
    hits: list[PatternHit] = []
    if i < 0 or not cs[i].complete:
        return hits
    c = cs[i]
    add = lambda n, d, bars, det=None: hits.append(PatternHit(n, d, i, bars, det or {}))
    if is_doji(c):
        add("doji", "NEUTRAL", 1)
    if is_hammer(c):
        add("hammer", "BULLISH", 1, {"close_loc": close_location(c)})
    if is_shooting_star(c):
        add("shooting_star", "BEARISH", 1, {"close_loc": close_location(c)})
    if is_bull_pin(c):
        add("bullish_pin_bar", "BULLISH", 1, {"close_loc": close_location(c)})
    if is_bear_pin(c):
        add("bearish_pin_bar", "BEARISH", 1, {"close_loc": close_location(c)})
    if i >= 1:
        p = cs[i - 1]
        if is_bull_engulfing(p, c):
            add("bullish_engulfing", "BULLISH", 2)
        if is_bear_engulfing(p, c):
            add("bearish_engulfing", "BEARISH", 2)
        if is_outside(p, c):
            add("outside_bar", "NEUTRAL", 2)
    if i >= 2:
        a, b = cs[i - 2], cs[i - 1]
        if is_morning_star(a, b, c):
            add("morning_star", "BULLISH", 3)
        if is_evening_star(a, b, c):
            add("evening_star", "BEARISH", 3)
        if is_three_white_soldiers(a, b, c):
            add("three_white_soldiers", "BULLISH", 3)
        if is_three_black_crows(a, b, c):
            add("three_black_crows", "BEARISH", 3)
        if is_inside(a, b):
            if c.close > b.high:
                add("inside_bar_bullish_breakout", "BULLISH", 3, {"inside_high": b.high})
            if c.close < b.low:
                add("inside_bar_bearish_breakdown", "BEARISH", 3, {"inside_low": b.low})
    f = detect_flag(cs, i, atr, True)
    if f:
        add("bull_flag", "BULLISH", f["pole_bars"] + f["flag_bars"] + 1, f)
    f = detect_flag(cs, i, atr, False)
    if f:
        add("bear_flag", "BEARISH", f["pole_bars"] + f["flag_bars"] + 1, f)
    d = detect_double(cs, pivots, i, atr, True)
    if d:
        add("double_bottom", "BULLISH", 0, d)
    d = detect_double(cs, pivots, i, atr, False)
    if d:
        add("double_top", "BEARISH", 0, d)
    t = detect_triangle(pivots, i, atr, True)
    if t:
        add("ascending_triangle", "BULLISH", 0, t)
    t = detect_triangle(pivots, i, atr, False)
    if t:
        add("descending_triangle", "BEARISH", 0, t)
    h = detect_hs(cs, pivots, i, atr, False)
    if h:
        add("head_and_shoulders", "BEARISH", 0, h)
    h = detect_hs(cs, pivots, i, atr, True)
    if h:
        add("inverse_head_and_shoulders", "BULLISH", 0, h)
    return hits


def recent_formations(cs, i, pivots, atr_series, lookback: int = FORMATION_LOOKBACK) -> list[PatternHit]:
    """Multi-bar formations completed within the last `lookback` candles up to and including i."""
    out = []
    names = BULLISH_FORMATIONS | BEARISH_FORMATIONS
    for k in range(max(0, i - lookback + 1), i + 1):
        for h in detect_at(cs, k, pivots, atr_series[k]):
            if h.name in names:
                out.append(h)
    return out
