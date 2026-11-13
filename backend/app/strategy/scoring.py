"""EXACT 100-point confidence score (Strategy V1.0).

| #  | Component                        | Max |
|----|----------------------------------|-----|
| 1  | EMA 9/20 trend alignment         | 10  |
| 2  | VWAP position                    | 10  |
| 3  | 15-minute market structure       | 10  |
| 4  | Support/resistance confluence    | 10  |
| 5  | Option-chain OI bias             | 10  |
| 6  | PCR                              |  5  |
| 7  | Delta/Gamma suitability          | 10  |
| 8  | IV/Theta suitability             |  5  |
| 9  | Rejection candle                 | 10  |
| 10 | Volume confirmation              |  5  |
| 11 | Fibonacci/trendline confluence   |  5  |
| 12 | Candle formation                 |  5  |
| 13 | Max Pain/OI context              |  5  |
|    | TOTAL                            | 100 |

No double counting:
* #4 counts S/R families at the reaction zone EXCLUDING fib and trendline; those score only in #11.
* #5 uses OI/premium/price evidence items only; PCR scores only in #6.
* #9 scores the single confirmation (rejection) candle; #12 scores only multi-bar formations from
  a separate list (see patterns.BULLISH_FORMATIONS), never the rejection pattern itself.
* #13 uses Max Pain and the distance to the opposing OI wall (room to move), not OI support at
  the entry zone (which already counts as a family in #4).
Missing or stale inputs give 0 points for that component (reason "data unavailable").
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

from app.strategy.levels import Confluence
from app.strategy.optionchain import ChainAnalysis, PCR_FLAT_EPS
from app.strategy.params import StrategyParams
from app.strategy.patterns import (PatternHit, BULLISH_REJECTION, BEARISH_REJECTION, BULLISH_FORMATIONS,
                                   BEARISH_FORMATIONS, close_location)

MAXES = {
    "ema_alignment": 10, "vwap_position": 10, "structure_15m": 10, "sr_confluence": 10, "oi_bias": 10,
    "pcr": 5, "delta_gamma": 10, "iv_theta": 5, "rejection_candle": 10, "volume": 5,
    "fib_trendline": 5, "candle_formation": 5, "maxpain_oi_context": 5,
}
NAMES = {
    "ema_alignment": "EMA 9/20 trend alignment", "vwap_position": "VWAP position",
    "structure_15m": "15-minute market structure", "sr_confluence": "Support/resistance confluence",
    "oi_bias": "Option-chain OI bias", "pcr": "PCR", "delta_gamma": "Delta/Gamma suitability",
    "iv_theta": "IV/Theta suitability", "rejection_candle": "Rejection candle", "volume": "Volume confirmation",
    "fib_trendline": "Fibonacci/trendline confluence", "candle_formation": "Candle formation",
    "maxpain_oi_context": "Max Pain/OI context",
}
assert sum(MAXES.values()) == 100


@dataclass
class Component:
    key: str
    name: str
    max: int
    points: int
    reasons: list[str]
    data_ok: bool = True


@dataclass
class ScoreBreakdown:
    total: int
    components: list[Component]
    band: str

    def to_dict(self):
        return {"total": self.total, "band": self.band, "components": [asdict(c) for c in self.components]}


def band(total: int, p: StrategyParams) -> str:
    if total >= p.strong_score:
        return "STRONG"
    if total >= p.min_score:
        return "VALID"
    if total >= 60:
        return "WAIT"
    return "AVOID"


@dataclass
class ScoreInputs:
    direction: str
    ema9_15: Optional[float]
    ema20_15: Optional[float]
    ema9_5: Optional[float]
    ema20_5: Optional[float]
    close: float
    vwap: Optional[float]
    atr: Optional[float]
    structure15: Optional[dict]               # StructureState.to_dict()
    zone_confluence: Optional[Confluence]     # at reaction zone, all families
    chain: Optional[ChainAnalysis]
    option: Optional[dict]                    # selected OptionQuote dict
    near_atm_gamma_max: Optional[float]
    iv_median: Optional[float]
    rejection_hits: list[PatternHit] = field(default_factory=list)
    confirm_candle: Optional[object] = None   # Candle
    volume: Optional[float] = None
    volume_sma: Optional[float] = None
    formations: list[PatternHit] = field(default_factory=list)
    risk_points: Optional[float] = None
    spot: Optional[float] = None


def _c(key, pts, reasons, ok=True) -> Component:
    pts = max(0, min(MAXES[key], int(pts)))
    return Component(key, NAMES[key], MAXES[key], pts, reasons, ok)


def score(si: ScoreInputs, p: StrategyParams) -> ScoreBreakdown:
    bull = si.direction == "BULLISH"
    comps: list[Component] = []
    NA = "data unavailable"

    # 1. EMA alignment: 5 pts 15m, 5 pts 5m
    pts, rs, ok = 0, [], True
    for tf, f, s in (("15m", si.ema9_15, si.ema20_15), ("5m", si.ema9_5, si.ema20_5)):
        if f is None or s is None:
            rs.append(f"{tf} EMA: {NA} (0/5)")
            ok = False
        elif (f > s) == bull and f != s:
            pts += 5
            rs.append(f"{tf} EMA9 {'above' if bull else 'below'} EMA20 (+5)")
        else:
            rs.append(f"{tf} EMA9/20 not aligned (0/5)")
    comps.append(_c("ema_alignment", pts, rs, ok))

    # 2. VWAP position: right side & not extended = 10; right side but > max_entry_ext_atr*ATR away = 5
    if si.vwap is None or si.atr is None:
        comps.append(_c("vwap_position", 0, [f"VWAP: {NA}"], False))
    else:
        dist = (si.close - si.vwap) if bull else (si.vwap - si.close)
        if dist > 0 and dist <= p.max_entry_ext_atr * si.atr:
            comps.append(_c("vwap_position", 10, [f"Close {'above' if bull else 'below'} VWAP by {dist:.2f} (<= {p.max_entry_ext_atr} ATR) (+10)"]))
        elif dist > 0:
            comps.append(_c("vwap_position", 5, [f"Correct side of VWAP but extended {dist:.2f} (> {p.max_entry_ext_atr} ATR) (+5)"]))
        else:
            comps.append(_c("vwap_position", 0, ["Close on wrong side of VWAP (0)"]))

    # 3. 15m structure: both HH&HL (LH&LL) = 10; one of two = 5
    st = si.structure15
    if st is None or st.get("higher_high") is None or st.get("higher_low") is None:
        comps.append(_c("structure_15m", 0, [f"15m swings: {NA} (need 2 confirmed highs and lows)"], False))
    else:
        hh, hl = st["higher_high"], st["higher_low"]
        good = [hh, hl] if bull else [not hh, not hl]
        n = sum(1 for g in good if g)
        lbl = ("higher high", "higher low") if bull else ("lower high", "lower low")
        rs = [f"{lbl[k]}: {'yes' if good[k] else 'no'}" for k in range(2)]
        comps.append(_c("structure_15m", 10 if n == 2 else 5 if n == 1 else 0, rs))

    # 4. S/R confluence (families excluding fib & trendline): 1 => 4, 2 => 7, >=3 => 10
    if si.zone_confluence is None:
        comps.append(_c("sr_confluence", 0, [f"Zone: {NA}"], False))
    else:
        fams = [f for f in si.zone_confluence.families if f not in ("fib", "trendline")]
        n = len(fams)
        pts = 10 if n >= 3 else 7 if n == 2 else 4 if n == 1 else 0
        comps.append(_c("sr_confluence", pts, [f"{n} independent source families at zone: {fams}"]))

    # 5. OI bias: supporting >=2 & opposing 0 => 10; >=1 & opposing 0 => 6; supporting > opposing => 3
    an = si.chain
    if an is None:
        comps.append(_c("oi_bias", 0, [f"Option chain: {NA}"], False))
    else:
        sup = an.bull_count if bull else an.bear_count
        opp = an.bear_count if bull else an.bull_count
        pts = 10 if (sup >= 2 and opp == 0) else 6 if (sup >= 1 and opp == 0) else 3 if sup > opp else 0
        present = [e.name for e in an.evidence if e.status == "present"]
        comps.append(_c("oi_bias", pts, [f"Supporting {sup}, opposing {opp} (INFERRED, basis={an.pcr_basis}); present: {present}"]))

    # 6. PCR: near-ATM PCR improving (bull) / deteriorating (bear) by > eps => 5; flat with level on right side of 1 => 2
    if an is None or an.pcr_change is None or an.pcr_near_atm is None:
        comps.append(_c("pcr", 0, [f"PCR change: {NA}"], False))
    else:
        ch = an.pcr_change if bull else -an.pcr_change
        if ch > PCR_FLAT_EPS:
            comps.append(_c("pcr", 5, [f"Near-ATM PCR {an.pcr_near_atm:.2f}, change {an.pcr_change:+.3f} ({'improving' if bull else 'deteriorating'}) (+5)"]))
        elif abs(an.pcr_change) <= PCR_FLAT_EPS and ((an.pcr_near_atm > 1) == bull):
            comps.append(_c("pcr", 2, [f"Near-ATM PCR {an.pcr_near_atm:.2f} flat, level supportive (+2)"]))
        else:
            comps.append(_c("pcr", 0, [f"Near-ATM PCR {an.pcr_near_atm:.2f}, change {an.pcr_change:+.3f} not supportive"]))

    # 7. Delta/Gamma: delta in band => 6; gamma >= 50% of max near-ATM gamma => 4
    o = si.option
    if o is None or o.get("delta") is None or o.get("gamma") is None:
        comps.append(_c("delta_gamma", 0, [f"Greeks: {NA}"], False))
    else:
        pts, rs = 0, []
        d = abs(o["delta"])
        if p.delta_min <= d <= p.delta_max:
            pts += 6
            rs.append(f"|delta| {d:.3f} in [{p.delta_min}, {p.delta_max}] (+6)")
        else:
            rs.append(f"|delta| {d:.3f} outside band (0/6)")
        if si.near_atm_gamma_max and o["gamma"] > 0 and o["gamma"] >= 0.5 * si.near_atm_gamma_max:
            pts += 4
            rs.append(f"gamma {o['gamma']:.5f} >= 50% of near-ATM max {si.near_atm_gamma_max:.5f} (+4)")
        else:
            rs.append("gamma not suitable or reference unavailable (0/4)")
        rs.append(f"Greeks source: {o.get('greeks_source')}")
        comps.append(_c("delta_gamma", pts, rs))

    # 8. IV/Theta: IV <= spike ratio * near-ATM median => 3; |theta|/premium <= limit => 2
    if o is None or o.get("iv") is None or o.get("theta") is None or not o.get("ltp") or si.iv_median is None:
        comps.append(_c("iv_theta", 0, [f"IV/Theta: {NA}"], False))
    else:
        pts, rs = 0, []
        if o["iv"] <= p.iv_spike_ratio * si.iv_median:
            pts += 3
            rs.append(f"IV {o['iv']:.2f} <= {p.iv_spike_ratio} x median {si.iv_median:.2f} (+3)")
        else:
            rs.append(f"IV spike: {o['iv']:.2f} vs median {si.iv_median:.2f} (0/3)")
        tp = abs(o["theta"]) / o["ltp"] * 100
        if tp <= p.max_theta_pct_of_premium:
            pts += 2
            rs.append(f"theta {tp:.2f}%/day of premium <= {p.max_theta_pct_of_premium}% (+2)")
        else:
            rs.append(f"theta {tp:.2f}%/day too high (0/2)")
        comps.append(_c("iv_theta", pts, rs))

    # 9. Rejection candle: recognised rejection pattern => 7; strong close location (>=0.75 / <=0.25) => +3
    allowed = BULLISH_REJECTION if bull else BEARISH_REJECTION
    rej = [h.name for h in si.rejection_hits if h.name in allowed]
    if si.confirm_candle is None:
        comps.append(_c("rejection_candle", 0, [f"Confirmation candle: {NA}"], False))
    elif not rej:
        comps.append(_c("rejection_candle", 0, ["No recognised rejection pattern on confirmation candle"]))
    else:
        cl = close_location(si.confirm_candle)
        strong = cl >= 0.75 if bull else cl <= 0.25
        comps.append(_c("rejection_candle", 10 if strong else 7,
                        [f"Patterns: {rej} (+7)", f"close location {cl:.2f} {'strong (+3)' if strong else '(0/3)'}"]))

    # 10. Volume: confirmation candle volume >= volume_confirm_mult * SMA20 => 5
    if si.volume is None or si.volume_sma is None or si.volume_sma <= 0 or si.volume <= 0:
        comps.append(_c("volume", 0, [f"Volume: {NA} (source: {p.volume_source})"], False))
    elif si.volume >= p.volume_confirm_mult * si.volume_sma:
        comps.append(_c("volume", 5, [f"Volume {si.volume:.0f} >= {p.volume_confirm_mult} x SMA20 {si.volume_sma:.0f} (+5)"]))
    else:
        comps.append(_c("volume", 0, [f"Volume {si.volume:.0f} < {p.volume_confirm_mult} x SMA20 {si.volume_sma:.0f}"]))

    # 11. Fib/trendline: fib at zone => 3; confirmed trendline at zone => 2
    if si.zone_confluence is None:
        comps.append(_c("fib_trendline", 0, [f"Zone: {NA}"], False))
    else:
        fams = si.zone_confluence.families
        pts = (3 if "fib" in fams else 0) + (2 if "trendline" in fams else 0)
        srcs = [s for s in si.zone_confluence.sources if s.startswith("fib") or s.startswith("trendline")]
        comps.append(_c("fib_trendline", pts, [f"At zone: {srcs or 'none'}"]))

    # 12. Candle formation: supporting multi-bar formation in last 6 bars and no opposing => 5
    sup_set, opp_set = (BULLISH_FORMATIONS, BEARISH_FORMATIONS) if bull else (BEARISH_FORMATIONS, BULLISH_FORMATIONS)
    sup = sorted({h.name for h in si.formations if h.name in sup_set})
    opp = sorted({h.name for h in si.formations if h.name in opp_set})
    if sup and not opp:
        comps.append(_c("candle_formation", 5, [f"Supporting formation(s): {sup} (+5)"]))
    else:
        comps.append(_c("candle_formation", 0, [f"Supporting: {sup or 'none'}; opposing: {opp or 'none'}"]))

    # 13. Max Pain / OI context: max pain on the trade side of spot => 3; opposing OI wall >= 1R away => 2
    if an is None or an.max_pain is None or si.spot is None or si.risk_points is None:
        comps.append(_c("maxpain_oi_context", 0, [f"Max Pain/OI: {NA}"], False))
    else:
        pts, rs = 0, []
        if (an.max_pain >= si.spot) if bull else (an.max_pain <= si.spot):
            pts += 3
            rs.append(f"Max Pain {an.max_pain:g} {'at/above' if bull else 'at/below'} spot {si.spot:.2f} (+3)")
        else:
            rs.append(f"Max Pain {an.max_pain:g} against direction (0/3)")
        wall = an.walls["resistance" if bull else "support"]
        if wall:
            w = wall[0]["strike"]
            room = (w - si.spot) if bull else (si.spot - w)
            if room >= si.risk_points:
                pts += 2
                rs.append(f"Opposing OI wall {w:g} is {room:.1f} pts away (>= 1R {si.risk_points:.1f}) (+2)")
            else:
                rs.append(f"Opposing OI wall {w:g} only {room:.1f} pts away (< 1R) (0/2)")
        else:
            rs.append("Opposing OI wall unavailable (0/2)")
        comps.append(_c("maxpain_oi_context", pts, rs))

    total = sum(c.points for c in comps)
    return ScoreBreakdown(total, comps, band(total, p))
