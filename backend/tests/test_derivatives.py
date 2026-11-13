"""Option chain, PCR, Max Pain, OI inference safeguards, Greeks and contract selection (SYNTHETIC fixtures)."""
import math
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest

from app.core.types import IST, OptionChainSnapshot, OptionQuote, OptionType, Index
from app.strategy.greeks import bs_price, bs_greeks, implied_vol, ensure_greeks, year_fraction
from app.strategy.optionchain import pcr, max_pain, analyse_chain, derivatives_confirms, atm_strike
from app.strategy.params import StrategyParams
from app.strategy.selection import select_contract
from tests.synthetic import chain_for

D = date(2026, 1, 5)
NOW = datetime.combine(D, time(11, 0), tzinfo=IST)


def mini_chain(rows, spot=100.0):
    return OptionChainSnapshot(Index.NIFTY, D + timedelta(days=3), spot, rows, "SYNTHETIC", NOW, NOW)


def q(k, ot, oi, **kw):
    return OptionQuote(strike=k, option_type=ot, oi=oi, **kw)


def test_pcr_and_max_pain_hand_computed():
    rows = [q(90, OptionType.CE, 10), q(90, OptionType.PE, 50), q(100, OptionType.CE, 30), q(100, OptionType.PE, 30),
            q(110, OptionType.CE, 60), q(110, OptionType.PE, 5)]
    ch = mini_chain(rows)
    assert pcr(ch) == pytest.approx(85 / 100)
    # pain(K) = sum CE_oi*max(K-s,0) + PE_oi*max(s-K,0)
    pains = {K: sum(o * max(K - s, 0) for s, o in [(90, 10), (100, 30), (110, 60)]) +
                sum(o * max(s - K, 0) for s, o in [(90, 50), (100, 30), (110, 5)]) for K in (90, 100, 110)}
    assert max_pain(ch) == min(pains, key=pains.get)
    assert atm_strike(ch) == 100


def test_missing_oi_gives_none_not_fabricated():
    ch = mini_chain([q(100, OptionType.CE, None), q(100, OptionType.PE, 10)])
    assert pcr(ch) is None and max_pain(ch) is None


def test_oi_change_alone_never_concludes_writing():
    """PE OI up near ATM but premium data missing => evidence 'unavailable', not 'present'."""
    ch = chain_for(24000, D, 23900, 24400, bullish=True)
    for r in ch.rows:
        r.prev_close = None
    an = analyse_chain(ch, None, 3, underlying_prev_close=23900)
    ev = {e.name: e for e in an.evidence}
    assert ev["put_support_strengthening"].status == "unavailable"
    assert ev["call_unwinding"].status == "unavailable"
    assert all(e.inferred for e in an.evidence)
    assert not derivatives_confirms(an, "BULLISH")


def test_oi_rising_with_rising_premium_is_not_put_writing():
    ch = chain_for(24000, D, 23900, 24400, bullish=True)
    for r in ch.rows:
        if r.option_type == OptionType.PE:
            r.prev_close = r.ltp * 0.8   # premium UP while OI up => not inferred writing
    an = analyse_chain(ch, None, 3, underlying_prev_close=23900)
    ev = {e.name: e for e in an.evidence}
    assert ev["put_support_strengthening"].status == "absent"


def test_bullish_and_bearish_inference():
    up = analyse_chain(chain_for(24000, D, 23900, 24400, bullish=True), None, 3, underlying_prev_close=23900)
    assert derivatives_confirms(up, "BULLISH") and not derivatives_confirms(up, "BEARISH")
    assert up.pcr_change > 0 and up.notes[0].startswith("OI interpretations are INFERRED")
    dn = analyse_chain(chain_for(24000, D, 23900, 24400, bullish=False), None, 3, underlying_prev_close=24100)
    assert derivatives_confirms(dn, "BEARISH")


def test_intraday_basis_and_wall_shift():
    a = chain_for(24000, D, 23800, 24300)
    b = chain_for(24050, D, 23800, 24400, received_at=NOW + timedelta(minutes=5))
    an = analyse_chain(b, a, 3, None)
    assert an.pcr_basis == "intraday"
    assert {e.name: e for e in an.evidence}["resistance_shifting_up"].status == "present"


# ---------------- Greeks ----------------
def test_put_call_parity_and_iv_roundtrip():
    S, K, T, r, s = 24000, 24000, 7 / 365, 0.065, 0.14
    c = bs_price(S, K, T, r, s, OptionType.CE)
    p = bs_price(S, K, T, r, s, OptionType.PE)
    assert c - p == pytest.approx(S - K * math.exp(-r * T), abs=1e-6)
    assert implied_vol(c, S, K, T, r, OptionType.CE) == pytest.approx(s, abs=1e-5)
    g = bs_greeks(S, K, T, r, s, OptionType.CE)
    assert 0.5 < g["delta"] < 0.56 and g["gamma"] > 0 and g["theta"] < 0


def test_ensure_greeks_never_overwrites_feed_and_labels_calculated():
    qf = OptionQuote(24000, OptionType.CE, delta=0.5, gamma=0.001, theta=-5, greeks_source="feed")
    ensure_greeks(qf, 24000, D + timedelta(days=7), NOW, 0.065)
    assert qf.delta == 0.5 and qf.greeks_source == "feed"
    qc = OptionQuote(24000, OptionType.CE, ltp=150, bid=149.5, ask=150.5)
    ensure_greeks(qc, 24000, D + timedelta(days=7), NOW, 0.065)
    assert qc.greeks_source == "calculated" and qc.delta is not None and "Black-Scholes" in qc.greeks_note
    qn = OptionQuote(24000, OptionType.CE)  # no price => no fabricated Greeks
    ensure_greeks(qn, 24000, D + timedelta(days=7), NOW, 0.065)
    assert qn.delta is None and qn.greeks_source is None


def test_year_fraction_expiry_at_1530():
    assert year_fraction(datetime.combine(D, time(15, 30), tzinfo=IST), D) == 0


# ---------------- selection ----------------
def test_selection_prefers_band_and_rejects_bad_liquidity():
    p = StrategyParams()
    ch = chain_for(24010, D, 23900, 24400, received_at=NOW)
    sel = select_contract(ch, "BULLISH", p, NOW, D)
    assert sel.ok and sel.selected.option_type == "CE" and sel.selected.moneyness in ("ATM", "ITM1")
    assert p.delta_min <= abs(sel.selected.quote["delta"]) <= p.delta_max
    # widen spreads on all CE => no selection
    for r in ch.rows:
        if r.option_type == OptionType.CE:
            r.bid, r.ask = r.ltp - 10, r.ltp + 10
    sel2 = select_contract(ch, "BULLISH", p, NOW, D)
    assert not sel2.ok and "spread_tight" in sel2.rationale


def test_selection_rejects_iv_spike_and_expired_contract():
    p = StrategyParams()
    ch = chain_for(24000, D, 23900, 24400, received_at=NOW)
    for r in ch.rows:
        if r.option_type == OptionType.PE and r.strike in (24000, 24050):
            r.iv = 40.0
    sel = select_contract(ch, "BEARISH", p, NOW, D)
    assert not sel.ok
    ch2 = chain_for(24000, D, 23900, 24400, received_at=NOW, expiry=D - timedelta(days=1))
    assert not select_contract(ch2, "BULLISH", p, NOW, D).ok
