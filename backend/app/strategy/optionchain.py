"""Option-chain and derivatives analysis.

CRITICAL RULE: OI change alone never proves writing or unwinding. Each evidence item combines the
underlying move, the option premium move and the OI change (plus volume and bid/ask where present),
and every conclusion is labelled INFERRED. Missing inputs make an item "unavailable"; they are never
filled in.

Change basis:
* "intraday": compared with an earlier snapshot of the same expiry taken this session (preferred).
* "day": compared with the previous-day OI / close supplied by the feed (prev_oi, prev_close).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

from app.core.types import OptionChainSnapshot, OptionType, OptionQuote

PCR_FLAT_EPS = 0.05          # |ΔPCR| <= 0.05 counts as unchanged
PRICE_FLAT_FRAC = 0.0005     # |underlying move| <= 0.05% counts as flat


def atm_strike(chain: OptionChainSnapshot) -> Optional[float]:
    ks = chain.strikes()
    if not ks:
        return None
    return min(ks, key=lambda k: (abs(k - chain.underlying_ltp), k))


def window_strikes(chain: OptionChainSnapshot, n: int) -> list[float]:
    ks = chain.strikes()
    atm = atm_strike(chain)
    if atm is None:
        return []
    i = ks.index(atm)
    return ks[max(0, i - n): i + n + 1]


def _sum(rows, ot, attr, strikes=None):
    vals = [getattr(r, attr) for r in rows if r.option_type == ot and (strikes is None or r.strike in strikes)]
    if not vals or any(v is None for v in vals):
        return None
    return float(sum(vals))


def pcr(chain: OptionChainSnapshot, strikes: Optional[list[float]] = None, attr: str = "oi") -> Optional[float]:
    s = set(strikes) if strikes is not None else None
    pe = _sum(chain.rows, OptionType.PE, attr, s)
    ce = _sum(chain.rows, OptionType.CE, attr, s)
    if pe is None or ce is None or ce == 0:
        return None
    return pe / ce


def max_pain(chain: OptionChainSnapshot) -> Optional[float]:
    ks = chain.strikes()
    ce = {r.strike: r.oi for r in chain.rows if r.option_type == OptionType.CE}
    pe = {r.strike: r.oi for r in chain.rows if r.option_type == OptionType.PE}
    if not ks or any(v is None for v in ce.values()) or any(v is None for v in pe.values()):
        return None
    best, best_val = None, None
    for k in ks:
        pain = sum(oi * max(k - s, 0.0) for s, oi in ce.items()) + sum(oi * max(s - k, 0.0) for s, oi in pe.items())
        if best_val is None or pain < best_val:
            best, best_val = k, pain
    return best


def oi_walls(chain: OptionChainSnapshot) -> dict:
    def top(ot):
        rows = [r for r in chain.rows if r.option_type == ot and r.oi is not None]
        rows.sort(key=lambda r: r.oi, reverse=True)
        return [{"strike": r.strike, "oi": r.oi, "oi_change": r.oi_change} for r in rows[:3]]
    return {"resistance": top(OptionType.CE), "support": top(OptionType.PE)}


@dataclass
class Evidence:
    name: str
    direction: str           # "BULLISH" | "BEARISH"
    status: str              # "present" | "absent" | "unavailable"
    inferred: bool = True
    basis: str = ""
    detail: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


@dataclass
class ChainAnalysis:
    atm: Optional[float]
    pcr_overall: Optional[float]
    pcr_near_atm: Optional[float]
    pcr_near_atm_prev: Optional[float]
    pcr_change: Optional[float]
    pcr_basis: str
    max_pain: Optional[float]
    walls: dict
    evidence: list[Evidence]
    bull_count: int
    bear_count: int
    underlying_change: Optional[float]
    notes: list[str]

    def to_dict(self):
        d = asdict(self)
        return d


def _prem_change(cur: OptionQuote, prev: Optional[OptionQuote]) -> Optional[float]:
    if cur.ltp is None:
        return None
    if prev is not None:
        return None if prev.ltp is None else cur.ltp - prev.ltp
    return None if cur.prev_close is None else cur.ltp - cur.prev_close


def _oi_change(cur: OptionQuote, prev: Optional[OptionQuote]) -> Optional[float]:
    if cur.oi is None:
        return None
    if prev is not None:
        return None if prev.oi is None else cur.oi - prev.oi
    return cur.oi_change


def analyse_chain(chain: OptionChainSnapshot, prev: Optional[OptionChainSnapshot], near_n: int,
                  underlying_prev_close: Optional[float], fut_price_change: Optional[float] = None,
                  fut_oi_change: Optional[float] = None) -> ChainAnalysis:
    notes = ["OI interpretations are INFERRED from combined price/premium/OI evidence, not certain."]
    if prev is not None and prev.expiry != chain.expiry:
        prev = None
        notes.append("Previous snapshot was for a different expiry; using day-change basis.")
    basis = "intraday" if prev is not None else "day"
    near = window_strikes(chain, near_n)
    near_set = set(near)
    atm = atm_strike(chain)

    pcr_all = pcr(chain)
    pcr_near = pcr(chain, near)
    if prev is not None:
        pcr_near_prev = pcr(prev, near)
    else:
        pe_prev = _sum(chain.rows, OptionType.PE, "prev_oi", near_set)
        ce_prev = _sum(chain.rows, OptionType.CE, "prev_oi", near_set)
        pcr_near_prev = (pe_prev / ce_prev) if (pe_prev is not None and ce_prev) else None
    pcr_chg = (pcr_near - pcr_near_prev) if (pcr_near is not None and pcr_near_prev is not None) else None

    if prev is not None:
        u_chg = chain.underlying_ltp - prev.underlying_ltp
    elif underlying_prev_close is not None:
        u_chg = chain.underlying_ltp - underlying_prev_close
    else:
        u_chg = None
    flat = (u_chg is not None and abs(u_chg) <= PRICE_FLAT_FRAC * chain.underlying_ltp)
    up = u_chg is not None and not flat and u_chg > 0
    down = u_chg is not None and not flat and u_chg < 0

    def agg(ot, side):
        """Aggregate OI change and premium change for near-ATM strikes on one side."""
        if side == "below":
            ks = [k for k in near if atm is not None and k <= atm]
        else:
            ks = [k for k in near if atm is not None and k >= atm]
        oi_c, pr_c, vol, at_bid = 0.0, 0.0, 0.0, 0
        n = 0
        for k in ks:
            cur = chain.get(k, ot)
            if cur is None:
                return None
            pv = prev.get(k, ot) if prev is not None else None
            oc, pc = _oi_change(cur, pv), _prem_change(cur, pv)
            if oc is None or pc is None:
                return None
            oi_c += oc
            pr_c += pc
            vol += cur.volume or 0.0
            if cur.mid is not None and cur.ltp is not None and cur.ltp <= cur.mid:
                at_bid += 1
            n += 1
        if n == 0:
            return None
        return {"strikes": ks, "oi_change": oi_c, "premium_change_sum": pr_c, "volume": vol, "ltp_at_or_below_mid": at_bid}

    pe_low = agg(OptionType.PE, "below")
    ce_high = agg(OptionType.CE, "above")
    ev: list[Evidence] = []

    def item(name, direction, cond, inputs, needs_underlying=True):
        if inputs is None or (needs_underlying and u_chg is None):
            ev.append(Evidence(name, direction, "unavailable", True, basis, {"reason": "missing inputs"}))
            return
        ev.append(Evidence(name, direction, "present" if cond else "absent", True, basis,
                           {**inputs, "underlying_change": u_chg}))

    # Bullish evidence
    item("put_support_strengthening", "BULLISH",
         pe_low is not None and pe_low["oi_change"] > 0 and pe_low["premium_change_sum"] < 0 and (up or flat), pe_low)
    item("call_unwinding", "BULLISH",
         ce_high is not None and ce_high["oi_change"] < 0 and ce_high["premium_change_sum"] > 0 and up, ce_high)
    # Bearish evidence
    item("call_resistance_strengthening", "BEARISH",
         ce_high is not None and ce_high["oi_change"] > 0 and ce_high["premium_change_sum"] < 0 and (down or flat), ce_high)
    item("put_unwinding", "BEARISH",
         pe_low is not None and pe_low["oi_change"] < 0 and pe_low["premium_change_sum"] > 0 and down, pe_low)

    # Wall shifts (intraday only — needs a previous snapshot)
    walls = oi_walls(chain)
    if prev is not None:
        pw = oi_walls(prev)
        r_now = walls["resistance"][0]["strike"] if walls["resistance"] else None
        r_prev = pw["resistance"][0]["strike"] if pw["resistance"] else None
        s_now = walls["support"][0]["strike"] if walls["support"] else None
        s_prev = pw["support"][0]["strike"] if pw["support"] else None
        ok_r = r_now is not None and r_prev is not None
        ok_s = s_now is not None and s_prev is not None
        item("resistance_shifting_up", "BULLISH", ok_r and r_now > r_prev,
             {"from": r_prev, "to": r_now} if ok_r else None, needs_underlying=False)
        item("support_shifting_down", "BEARISH", ok_s and s_now < s_prev,
             {"from": s_prev, "to": s_now} if ok_s else None, needs_underlying=False)
    else:
        ev.append(Evidence("resistance_shifting_up", "BULLISH", "unavailable", True, basis, {"reason": "needs intraday snapshot"}))
        ev.append(Evidence("support_shifting_down", "BEARISH", "unavailable", True, basis, {"reason": "needs intraday snapshot"}))

    # Underlying price / futures OI behaviour (long build-up / short build-up)
    if fut_price_change is not None and fut_oi_change is not None:
        ev.append(Evidence("long_buildup_underlying", "BULLISH",
                           "present" if (fut_price_change > 0 and fut_oi_change > 0) else "absent", True, basis,
                           {"fut_price_change": fut_price_change, "fut_oi_change": fut_oi_change}))
        ev.append(Evidence("short_buildup_underlying", "BEARISH",
                           "present" if (fut_price_change < 0 and fut_oi_change > 0) else "absent", True, basis,
                           {"fut_price_change": fut_price_change, "fut_oi_change": fut_oi_change}))
    else:
        ev.append(Evidence("long_buildup_underlying", "BULLISH", "unavailable", True, basis, {"reason": "futures OI not supplied"}))
        ev.append(Evidence("short_buildup_underlying", "BEARISH", "unavailable", True, basis, {"reason": "futures OI not supplied"}))

    bull = sum(1 for e in ev if e.direction == "BULLISH" and e.status == "present")
    bear = sum(1 for e in ev if e.direction == "BEARISH" and e.status == "present")
    return ChainAnalysis(atm, pcr_all, pcr_near, pcr_near_prev, pcr_chg, basis, max_pain(chain), walls, ev,
                         bull, bear, u_chg, notes)


def derivatives_confirms(an: ChainAnalysis, direction: str) -> bool:
    """Mandatory derivatives confirmation: at least one present evidence item in the trade direction and
    strictly more supporting than opposing items."""
    if direction == "BULLISH":
        return an.bull_count >= 1 and an.bull_count > an.bear_count
    return an.bear_count >= 1 and an.bear_count > an.bull_count
