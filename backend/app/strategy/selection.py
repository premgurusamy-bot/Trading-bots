"""Option contract selection: ATM or one strike ITM in the trade direction, never by premium alone.

Every candidate is checked for delta band, positive gamma, theta decay, IV spike, volume, OI,
bid/ask spread, two-sided quote, expiry validity and Greeks availability. The best passing
candidate has delta closest to the band centre, with ties broken by tighter spread %.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from statistics import median
from typing import Optional

from app.core.types import OptionChainSnapshot, OptionQuote, OptionType
from app.strategy.greeks import ensure_greeks
from app.strategy.optionchain import atm_strike, window_strikes
from app.strategy.params import StrategyParams


@dataclass
class CandidateCheck:
    strike: float
    option_type: str
    moneyness: str                # "ATM" | "ITM1"
    checks: dict
    passed: bool
    quote: dict
    spread_pct: Optional[float]
    rank_key: Optional[tuple] = None

    def to_dict(self):
        d = asdict(self)
        d.pop("rank_key", None)
        return d


@dataclass
class Selection:
    selected: Optional[CandidateCheck]
    candidates: list[CandidateCheck] = field(default_factory=list)
    rationale: str = ""
    near_atm_iv_median: Optional[float] = None

    @property
    def ok(self) -> bool:
        return self.selected is not None

    def to_dict(self):
        return {"selected": self.selected.to_dict() if self.selected else None,
                "candidates": [c.to_dict() for c in self.candidates], "rationale": self.rationale,
                "near_atm_iv_median": self.near_atm_iv_median}


def select_contract(chain: OptionChainSnapshot, direction: str, p: StrategyParams, now: datetime,
                    today: date) -> Selection:
    ot = OptionType.CE if direction == "BULLISH" else OptionType.PE
    atm = atm_strike(chain)
    if atm is None:
        return Selection(None, [], "No strikes in chain")
    ks = chain.strikes()
    i = ks.index(atm)
    # one strike ITM: lower strike for CE, higher strike for PE
    itm_i = i - 1 if ot == OptionType.CE else i + 1
    cands = [("ATM", atm)]
    if 0 <= itm_i < len(ks):
        cands.append(("ITM1", ks[itm_i]))
    near = window_strikes(chain, p.near_atm_pcr_strikes)
    ivs = []
    for k in near:
        q = chain.get(k, ot)
        if q is not None:
            ensure_greeks(q, chain.underlying_ltp, chain.expiry, now, p.risk_free_rate)
            if q.iv is not None and q.iv > 0:
                ivs.append(q.iv)
    iv_med = median(ivs) if ivs else None

    results: list[CandidateCheck] = []
    for mny, k in cands:
        q = chain.get(k, ot)
        if q is None:
            continue
        ensure_greeks(q, chain.underlying_ltp, chain.expiry, now, p.risk_free_rate)
        d = q.delta
        sp_pct = (q.spread / q.mid * 100) if (q.spread is not None and q.mid) else None
        c = {
            "expiry_active": chain.expiry >= today,
            "contract_identified": q.security_id is not None,
            "greeks_available": d is not None and q.gamma is not None and q.theta is not None,
            "delta_in_band": d is not None and p.delta_min <= abs(d) <= p.delta_max and (d > 0) == (ot == OptionType.CE),
            "gamma_positive": q.gamma is not None and q.gamma > p.min_gamma,
            "theta_acceptable": (q.theta is not None and q.ltp is not None and q.ltp > 0
                                 and abs(q.theta) / q.ltp * 100 <= p.max_theta_pct_of_premium),
            "iv_not_spiking": q.iv is not None and iv_med is not None and q.iv <= p.iv_spike_ratio * iv_med,
            "volume_sufficient": q.volume is not None and q.volume >= p.min_option_volume,
            "oi_sufficient": q.oi is not None and q.oi >= p.min_option_oi,
            "two_sided_quote": q.mid is not None,
            "spread_tight": q.spread is not None and sp_pct is not None and (sp_pct <= p.max_spread_pct or q.spread <= p.max_spread_abs),
            "premium_available": q.ltp is not None and q.ltp > 0,
        }
        passed = all(c.values())
        rk = (abs(abs(d) - (p.delta_min + p.delta_max) / 2), sp_pct) if passed else None
        results.append(CandidateCheck(k, ot.value, mny, c, passed, q.to_dict(), sp_pct, rk))
    passing = [r for r in results if r.passed]
    if not passing:
        fails = {r.moneyness: [k for k, v in r.checks.items() if not v] for r in results}
        return Selection(None, results, f"No candidate passed all checks: {fails}", iv_med)
    best = min(passing, key=lambda r: r.rank_key)
    why = (f"{best.moneyness} {best.option_type} {best.strike:g} selected: delta {best.quote['delta']:.3f} "
           f"closest to band centre {(p.delta_min + p.delta_max) / 2:.2f}; spread {best.spread_pct:.2f}%; "
           f"Greeks source: {best.quote.get('greeks_source')}")
    return Selection(best, results, why, iv_med)
