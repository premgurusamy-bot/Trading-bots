"""Support / resistance engine.

Every level records source, family, price/zone, timestamp, confirmation status and strength.
Confluence counts *independent source families*: two levels from the same family (e.g. two swing
lows) count once. 2 families overlapping = important confluence; 3+ = high confluence.
Only information available at evaluation bar i is used.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, date
from typing import Optional, Sequence

from app.core.types import Candle, OptionChainSnapshot, OptionType
from app.strategy import indicators as ind
from app.strategy.params import StrategyParams
from app.strategy.structure import Pivot, confirmed_pivots

FAMILIES = ("prev_day", "opening_range", "swing", "zone", "fib", "vwap", "ema20", "oi", "trendline")


@dataclass
class Level:
    source: str              # e.g. "PDH", "ORL", "swing_low_5m", "demand_zone", "fib_61.8", "VWAP"
    family: str              # one of FAMILIES
    low: float               # zone low (== high for a line level)
    high: float
    kind: str                # "support" | "resistance" | "both"
    ts: Optional[datetime]
    confirmed: bool
    strength: float = 1.0
    meta: dict = field(default_factory=dict)

    @property
    def price(self) -> float:
        return (self.low + self.high) / 2

    def distance(self, x: float) -> float:
        if self.low <= x <= self.high:
            return 0.0
        return min(abs(x - self.low), abs(x - self.high))

    def to_dict(self):
        d = asdict(self)
        d["ts"] = self.ts.isoformat() if self.ts else None
        d["price"] = self.price
        return d


def previous_day_levels(daily_prev: Optional[Candle]) -> list[Level]:
    if daily_prev is None:
        return []
    ts = daily_prev.ts
    return [
        Level("PDH", "prev_day", daily_prev.high, daily_prev.high, "resistance", ts, True, 2.0),
        Level("PDL", "prev_day", daily_prev.low, daily_prev.low, "support", ts, True, 2.0),
        Level("PDC", "prev_day", daily_prev.close, daily_prev.close, "both", ts, True, 1.0),
    ]


def previous_day_from_intraday(candles: Sequence[Candle], today: date) -> Optional[Candle]:
    """Derive the previous session's daily OHLC from intraday candles (genuine data only)."""
    prev = [c for c in candles if c.ts.date() < today]
    if not prev:
        return None
    d = max(c.ts.date() for c in prev)
    day = [c for c in prev if c.ts.date() == d]
    return Candle(ts=day[0].ts, open=day[0].open, high=max(c.high for c in day), low=min(c.low for c in day),
                  close=day[-1].close, volume=sum(c.volume for c in day), source=day[0].source)


def opening_range(c5: Sequence[Candle], i: int, minutes: int) -> list[Level]:
    day = c5[i].ts.date()
    first = [c for c in c5[:i + 1] if c.ts.date() == day]
    if not first:
        return []
    start = first[0].ts.replace(hour=9, minute=15, second=0, microsecond=0)
    end = start + timedelta(minutes=minutes)
    inside = [c for c in first if c.ts < end]
    confirmed = c5[i].ts + timedelta(minutes=5) >= end and all(c.complete for c in inside)
    if not inside or not confirmed:
        return []
    h = max(c.high for c in inside)
    l = min(c.low for c in inside)
    return [Level("ORH", "opening_range", h, h, "resistance", start, True, 1.5),
            Level("ORL", "opening_range", l, l, "support", start, True, 1.5)]


def swing_levels(pivots: Sequence[Pivot], i: int, tf: str, keep: int = 4) -> list[Level]:
    known = [p for p in pivots if p.confirm_index <= i]
    hs = [p for p in known if p.kind == "H"][-keep:]
    ls = [p for p in known if p.kind == "L"][-keep:]
    out = [Level(f"swing_high_{tf}", "swing", p.price, p.price, "resistance", p.ts, True,
                 1.5 if tf == "15m" else 1.0, {"index": p.index}) for p in hs]
    out += [Level(f"swing_low_{tf}", "swing", p.price, p.price, "support", p.ts, True,
                  1.5 if tf == "15m" else 1.0, {"index": p.index}) for p in ls]
    return out


def supply_demand_zones(c5: Sequence[Candle], atr5: ind.Series, i: int, impulse_atr: float = 1.5,
                        keep: int = 3) -> list[Level]:
    """Demand zone: base candle immediately before a bullish impulse candle (body >= impulse_atr*ATR and
    close above the base high). Zone = [base low, base body top]. Supply mirrors this. A zone is
    confirmed once the impulse candle is complete and is dropped once a later completed candle closes
    through it."""
    zones: list[Level] = []
    for k in range(1, i + 1):
        imp, base = c5[k], c5[k - 1]
        a = atr5[k]
        if a is None or not imp.complete:
            continue
        if imp.bullish and imp.body >= impulse_atr * a and imp.close > base.high:
            z = Level("demand_zone", "zone", base.low, max(base.open, base.close), "support", base.ts, True, 1.5)
            if not any(c5[m].complete and c5[m].close < z.low for m in range(k + 1, i + 1)):
                zones.append(z)
        if imp.bearish and imp.body >= impulse_atr * a and imp.close < base.low:
            z = Level("supply_zone", "zone", min(base.open, base.close), base.high, "resistance", base.ts, True, 1.5)
            if not any(c5[m].complete and c5[m].close > z.high for m in range(k + 1, i + 1)):
                zones.append(z)
    d = [z for z in zones if z.kind == "support"][-keep:]
    s = [z for z in zones if z.kind == "resistance"][-keep:]
    return d + s


def fib_levels(pivots: Sequence[Pivot], i: int, ratios: Sequence[float]) -> list[Level]:
    """Retracement of the most recent confirmed swing leg (last confirmed H and L)."""
    known = [p for p in pivots if p.confirm_index <= i]
    hs = [p for p in known if p.kind == "H"]
    ls = [p for p in known if p.kind == "L"]
    if not hs or not ls:
        return []
    h, l = hs[-1], ls[-1]
    rng = h.price - l.price
    if rng <= 0:
        return []
    out = []
    if l.index < h.index:   # up-leg: retracement levels act as support
        for r in ratios:
            px = h.price - r * rng
            out.append(Level(f"fib_{r*100:.1f}", "fib", px, px, "support", h.ts, True, 1.0,
                             {"leg": "up", "from": l.price, "to": h.price}))
    else:                   # down-leg: retracement levels act as resistance
        for r in ratios:
            px = l.price + r * rng
            out.append(Level(f"fib_{r*100:.1f}", "fib", px, px, "resistance", l.ts, True, 1.0,
                             {"leg": "down", "from": h.price, "to": l.price}))
    return out


def trendlines(c5: Sequence[Candle], pivots: Sequence[Pivot], i: int, atr_now: Optional[float]) -> list[Level]:
    """Confirmed trendline: through the last two confirmed swing lows (rising => support) or swing
    highs (falling => resistance), with no completed close through the line between the anchors
    and the evaluation bar. Value is projected to bar i."""
    out = []
    known = [p for p in pivots if p.confirm_index <= i]
    for kind, want_rising, lvl_kind in (("L", True, "support"), ("H", False, "resistance")):
        ps = [p for p in known if p.kind == kind]
        if len(ps) < 2:
            continue
        a, b = ps[-2], ps[-1]
        if b.index == a.index:
            continue
        m = (b.price - a.price) / (b.index - a.index)
        if (m > 0) != want_rising or m == 0:
            continue
        broken = False
        for k in range(a.index, i + 1):
            line = a.price + m * (k - a.index)
            c = c5[k]
            if not c.complete:
                continue
            if lvl_kind == "support" and c.close < line:
                broken = True
            if lvl_kind == "resistance" and c.close > line:
                broken = True
        if broken:
            continue
        val = a.price + m * (i - a.index)
        out.append(Level(f"trendline_{lvl_kind}", "trendline", val, val, lvl_kind, b.ts, True, 1.5,
                         {"anchor1": a.to_dict(), "anchor2": b.to_dict(), "slope_per_bar": m}))
    return out


def dynamic_levels(vwap: Optional[float], ema20: Optional[float], ts: datetime) -> list[Level]:
    out = []
    if vwap is not None:
        out.append(Level("VWAP", "vwap", vwap, vwap, "both", ts, True, 1.5))
    if ema20 is not None:
        out.append(Level("EMA20", "ema20", ema20, ema20, "both", ts, True, 1.0))
    return out


def oi_levels(chain: Optional[OptionChainSnapshot], top: int = 2) -> list[Level]:
    """Highest PE OI strikes = support, highest CE OI strikes = resistance (from genuine chain only)."""
    if chain is None:
        return []
    out = []
    for ot, kind, src in ((OptionType.PE, "support", "oi_put_support"), (OptionType.CE, "resistance", "oi_call_resistance")):
        rows = [r for r in chain.rows if r.option_type == ot and r.oi is not None]
        rows.sort(key=lambda r: r.oi, reverse=True)
        for rank, r in enumerate(rows[:top]):
            out.append(Level(src, "oi", r.strike, r.strike, kind, chain.feed_ts or chain.received_at, True,
                             2.0 if rank == 0 else 1.0, {"oi": r.oi, "rank": rank + 1}))
    return out


@dataclass
class Confluence:
    families: list[str]
    sources: list[str]
    count: int
    label: str          # "none" | "single" | "important" | "high"
    levels: list[Level]

    def to_dict(self):
        return {"families": self.families, "sources": self.sources, "count": self.count, "label": self.label,
                "levels": [l.to_dict() for l in self.levels]}


def confluence_at(levels: Sequence[Level], price: float, tol: float, kinds: tuple[str, ...]) -> Confluence:
    near = [l for l in levels if l.kind in kinds and l.distance(price) <= tol]
    fams = sorted({l.family for l in near})
    n = len(fams)
    label = "none" if n == 0 else "single" if n == 1 else "important" if n == 2 else "high"
    return Confluence(fams, [l.source for l in near], n, label, near)


def build_levels(c5: Sequence[Candle], c15: Sequence[Candle], i: int, j15: Optional[int], cache: dict,
                 p: StrategyParams, chain: Optional[OptionChainSnapshot], daily_prev: Optional[Candle]) -> list[Level]:
    atr5 = cache["atr5"]
    piv5 = cache["piv5"]
    piv15 = cache["piv15"]
    lv: list[Level] = []
    lv += previous_day_levels(daily_prev)
    lv += opening_range(c5, i, p.opening_range_minutes)
    lv += swing_levels(piv5, i, "5m")
    if j15 is not None:
        lv += swing_levels(piv15, j15, "15m")
    lv += supply_demand_zones(c5, atr5, i)
    lv += fib_levels(piv5, i, p.fib_levels)
    lv += trendlines(c5, piv5, i, atr5[i])
    lv += dynamic_levels(cache["vwap5"][i], cache["e20_5"][i], c5[i].ts)
    lv += oi_levels(chain)
    return lv


def next_level_beyond(levels: Sequence[Level], price: float, direction: str, min_gap: float = 0.0) -> Optional[Level]:
    """Nearest major opposing level beyond `price` (resistance above for bullish, support below for bearish).
    "Major" = static families (prev_day, opening_range, swing, zone, oi, trendline)."""
    major = {"prev_day", "opening_range", "swing", "zone", "oi", "trendline"}
    if direction == "BULLISH":
        cands = [l for l in levels if l.family in major and l.kind in ("resistance", "both") and l.low > price + min_gap]
        return min(cands, key=lambda l: l.low, default=None)
    cands = [l for l in levels if l.family in major and l.kind in ("support", "both") and l.high < price - min_gap]
    return max(cands, key=lambda l: l.high, default=None)
