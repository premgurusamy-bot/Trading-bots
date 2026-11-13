"""Unit tests on SYNTHETIC fixtures: pivots, S/R, fibonacci, patterns, trend filters, candles."""
from datetime import date, timedelta, datetime

import pytest

from app.core.types import IST
from app.strategy import indicators as ind
from app.strategy.candles import resample, bucket_start, TickCandleBuilder
from app.strategy.levels import (fib_levels, previous_day_levels, confluence_at, Level, opening_range, oi_levels,
                                 previous_day_from_intraday)
from app.strategy.patterns import (is_hammer, is_shooting_star, is_bull_engulfing, is_bear_engulfing, is_morning_star,
                                   is_evening_star, is_three_white_soldiers, is_three_black_crows, is_doji, is_bull_pin,
                                   is_bear_pin, is_outside, detect_at, detect_flag, detect_double, detect_hs,
                                   detect_triangle)
from app.strategy.params import StrategyParams
from app.strategy.structure import confirmed_pivots, structure_state, evaluate_trend
from tests.synthetic import c, ts, path_candles, uptrend_closes, chain_for

D = date(2026, 1, 5)


def zig(n=75):
    return path_candles(D, uptrend_closes(n, 1000.0, up=5, down=-3, up_bars=8, down_bars=6), wick=1)


def test_pivots_confirmed_only_after_n_bars_no_repaint():
    cs = zig()
    full = confirmed_pivots(cs, 5)
    assert full, "expected pivots"
    for p in full:
        assert p.confirm_index == p.index + 5
    # Evaluating on any prefix yields exactly the pivots confirmed by then, with identical values
    for upto in range(20, len(cs)):
        pref = confirmed_pivots(cs[:upto + 1], 5)
        assert pref == [p for p in full if p.confirm_index <= upto]


def test_structure_uptrend_and_breakdown():
    cs = zig()
    piv = confirmed_pivots(cs, 5)
    st = structure_state(cs, piv, len(cs) - 1)
    assert st.trend == "up" and st.higher_high and st.higher_low and not st.bearish_breakdown
    last_low = st.last_low.price
    t = cs[-1].ts + timedelta(minutes=5)
    crash = cs + [c(t, cs[-1].close, cs[-1].close, last_low - 20, last_low - 10)]
    st2 = structure_state(crash, piv, len(crash) - 1)
    assert st2.bearish_breakdown


def test_fibonacci_levels_up_leg():
    cs = zig()
    piv = confirmed_pivots(cs, 5)
    fl = fib_levels(piv, len(cs) - 1, (0.382, 0.5, 0.618, 0.786))
    assert len(fl) == 4
    hi = [p for p in piv if p.kind == "H"][-1].price
    lo = [p for p in piv if p.kind == "L"][-1].price
    if fl[0].kind == "support":
        assert fl[1].price == pytest.approx(hi - 0.5 * (hi - lo))
    else:
        assert fl[1].price == pytest.approx(lo + 0.5 * abs(hi - lo))


def test_previous_day_and_opening_range():
    d1 = date(2026, 1, 5)
    cs = path_candles(d1, [100 + k for k in range(75)])
    dp = previous_day_from_intraday(cs + path_candles(date(2026, 1, 6), [200.0] * 5), date(2026, 1, 6))
    lv = {l.source: l for l in previous_day_levels(dp)}
    assert lv["PDH"].price == max(x.high for x in cs) and lv["PDL"].price == min(x.low for x in cs)
    assert lv["PDC"].price == cs[-1].close
    # OR: first 15 minutes = 3 candles; not available before they complete
    assert opening_range(cs, 1, 15) == []
    orl = {l.source: l.price for l in opening_range(cs, 2, 15)}
    assert orl["ORH"] == max(x.high for x in cs[:3])


def test_confluence_counts_independent_families():
    t = ts(D, 10, 0)
    lv = [Level("swing_low_5m", "swing", 100, 100, "support", t, True), Level("swing_low_15m", "swing", 100.5, 100.5, "support", t, True),
          Level("VWAP", "vwap", 100.2, 100.2, "both", t, True), Level("oi_put_support", "oi", 100, 100, "support", t, True)]
    cf = confluence_at(lv, 100.1, 1.0, ("support", "both"))
    assert cf.count == 3 and cf.label == "high"
    cf2 = confluence_at(lv[:2], 100.1, 1.0, ("support", "both"))
    assert cf2.count == 1   # same family counts once
    cf3 = confluence_at(lv[:3], 100.1, 1.0, ("support", "both"))
    assert cf3.label == "important"


def test_oi_levels_from_chain():
    ch = chain_for(24000, D, put_wall=23800, call_wall=24300)
    ol = oi_levels(ch)
    sup = [l for l in ol if l.kind == "support"][0]
    res = [l for l in ol if l.kind == "resistance"][0]
    assert sup.price == 23800 and res.price == 24300


# ---------------- candle patterns ----------------
T0 = ts(D, 10, 0)


def k(i, o, h, l, cl):
    return c(T0 + timedelta(minutes=5 * i), o, h, l, cl)


def test_single_candle_patterns():
    assert is_hammer(k(0, 100, 101, 90, 100.8))
    assert not is_hammer(k(0, 100, 105, 90, 101))
    assert is_shooting_star(k(0, 100, 110, 99, 99.3))
    assert is_doji(k(0, 100, 105, 95, 100.5))
    assert is_bull_pin(k(0, 100, 101, 90, 100.5))
    assert is_bear_pin(k(0, 100, 110, 99, 99.5))


def test_two_three_candle_patterns():
    assert is_bull_engulfing(k(0, 105, 106, 99, 100), k(1, 99.5, 107, 99, 106))
    assert is_bear_engulfing(k(0, 100, 106, 99, 105), k(1, 105.5, 106, 98, 99))
    assert is_morning_star(k(0, 110, 111, 99, 100), k(1, 100, 101, 98, 99.5), k(2, 100, 108, 99.5, 107))
    assert is_evening_star(k(0, 100, 111, 99, 110), k(1, 110, 112, 109, 110.5), k(2, 110, 110.5, 102, 103))
    assert is_three_white_soldiers(k(0, 100, 106, 99.8, 105.8), k(1, 103, 109, 102.8, 108.8), k(2, 106, 112, 105.8, 111.8))
    assert is_three_black_crows(k(0, 112, 112.2, 106, 106.2), k(1, 109, 109.2, 103, 103.2), k(2, 106, 106.2, 100, 100.2))
    assert is_outside(k(0, 100, 102, 99, 101), k(1, 101, 103, 98, 99))


def test_inside_bar_breakout_detection():
    cs = [k(0, 100, 110, 90, 105), k(1, 104, 106, 96, 100), k(2, 100, 108, 99, 107.5)]
    names = {h.name for h in detect_at(cs, 2, [], 5.0)}
    assert "inside_bar_bullish_breakout" in names


def test_flag_detection():
    cs = [k(0, 100, 101, 99, 100), k(1, 100, 106, 99.5, 105.5), k(2, 105.5, 111, 105, 110.5),
          k(3, 110.5, 111, 108, 109), k(4, 109, 110, 107.5, 108), k(5, 108, 113, 107.8, 112.5)]
    assert detect_flag(cs, 5, 3.0, True) is not None
    assert detect_flag(cs, 5, 30.0, True) is None   # pole too small vs ATR


def test_double_bottom_triangle_hs_on_zigzag():
    # synthetic W: down, up, down to same low, up through neckline
    closes = ([100 - 2 * j for j in range(8)] + [86 + 2 * j for j in range(7)] + [98 - 2 * j for j in range(7)]
              + [86 + 2 * j for j in range(12)])
    cs = path_candles(D, closes, wick=0.5)
    piv = confirmed_pivots(cs, 3)
    hits = []
    for i in range(len(cs)):
        if detect_double(cs, piv, i, 4.0, True):
            hits.append(i)
    assert hits, "double bottom should confirm on neckline break"


def test_patterns_need_completed_candle():
    cs = [c(T0, 100, 101, 90, 100.8, complete=False)]
    assert detect_at(cs, 0, [], 5.0) == []


# ---------------- candles ----------------
def test_resample_session_anchor_and_completeness():
    d = date(2026, 1, 5)
    ones = [c(ts(d, 9, 15) + timedelta(minutes=m), 100 + m, 101 + m, 99 + m, 100.5 + m) for m in range(17)]
    five = resample(ones, 5, now=ts(d, 9, 32, ) if False else ts(d, 9, 32))
    assert [x.ts.minute for x in five] == [15, 20, 25, 30]
    assert five[0].open == 100 and five[0].close == 104.5 and five[0].high == 105
    assert all(x.complete for x in five[:3]) and not five[3].complete
    assert bucket_start(ts(d, 9, 29), 15).minute == 15 and bucket_start(ts(d, 9, 30), 15).minute == 30


def test_tick_builder_ignores_late_ticks():
    b = TickCandleBuilder("TEST")
    d = date(2026, 1, 5)
    assert b.on_tick(100, ts(d, 9, 15)) is None
    b.on_tick(101, ts(d, 9, 15) + timedelta(seconds=30))
    done = b.on_tick(102, ts(d, 9, 16))
    assert done and done.close == 101 and done.complete
    assert b.on_tick(50, ts(d, 9, 15) + timedelta(seconds=59)) is None   # late tick ignored
    assert b.partial().low == 102


# ---------------- trend / sideways ----------------
def test_trend_filter_bullish_and_sideways():
    p = StrategyParams()
    up = path_candles(D, uptrend_closes(75, 1000, up=4, down=-2), wick=2)
    c15 = resample(up, 15)
    te = evaluate_trend(up, c15, len(up) - 1, ind.session_vwap(up), p)
    assert te.direction == "BULLISH" and not te.sideways
    chop = path_candles(D, [1000 + (3 if j % 2 else -3) for j in range(75)], wick=2)
    te2 = evaluate_trend(chop, resample(chop, 15), len(chop) - 1, ind.session_vwap(chop), p)
    assert te2.sideways and te2.direction is None
