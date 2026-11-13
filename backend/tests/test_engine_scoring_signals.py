"""Scoring (exact 100), mandatory overrides, setups A/B/C, signal lifecycle (SYNTHETIC fixtures)."""
from dataclasses import replace
from datetime import timedelta

import pytest

from app.core.types import Candle, DataQuality
from app.strategy import indicators as ind
from app.strategy.engine import evaluate, MarketContext, still_valid
from app.strategy.params import StrategyParams, STRATEGY_VERSION
from app.strategy.scoring import MAXES, score, ScoreInputs
from app.strategy.setups import build_plan, premium_plan, find_breakout_retest
from app.strategy.signals import SignalBook, SignalState, expiry_time
from tests.synthetic import bullish_setup_a, chain_for, ok_quality, c, path_candles, ts

P = StrategyParams()


def scenario(quality=None, risk_ok=True, mirror=False, **chain_kw):
    c5, c15, dp, conf = bullish_setup_a()
    if mirror:
        c5, c15, dp, conf = mirror_down(c5)
    e20 = ind.ema([x.close for x in c5], 20)[-1]
    spot = conf.close
    now = conf.ts + timedelta(minutes=5, seconds=10)
    if not mirror:
        ch = chain_for(spot, conf.ts.date(), put_wall=round(e20 / 50) * 50, call_wall=round(spot / 50) * 50 + 400,
                       received_at=now - timedelta(seconds=3), mp_strike=round(spot / 50) * 50 + 50, **chain_kw)
    else:
        ch = chain_for(spot, conf.ts.date(), put_wall=round(spot / 50) * 50 - 400, call_wall=round(e20 / 50) * 50,
                       bullish=False, received_at=now - timedelta(seconds=3), **chain_kw)
        # max pain at/below spot for bearish: heavy CE OI below spot
        for r in ch.rows:
            if r.option_type.value == "CE" and r.strike == round(spot / 50) * 50 - 50:
                r.oi += 400000.0
                r.prev_oi += 440000.0
    ctx = MarketContext("NIFTY", c5, c15, ch, None, dp, now, quality or ok_quality(),
                        {"ok": risk_ok, "reasons": [] if risk_ok else ["daily limit"]}, underlying_prev_close=dp.close)
    return ctx


def mirror_down(c5):
    """Reflect the synthetic uptrend around a pivot price => synthetic downtrend with a shooting star."""
    M = 2 * 24000.0
    out = [Candle(x.ts, M - x.open, M - x.low, M - x.high, M - x.close, x.volume, x.complete, x.source) for x in c5]
    from app.strategy.candles import resample
    from app.strategy.levels import previous_day_from_intraday
    return out, resample(out, 15), previous_day_from_intraday(out, out[-1].ts.date()), out[-1]


def test_score_maxes_total_exactly_100():
    assert sum(MAXES.values()) == 100 and len(MAXES) == 13


def test_missing_data_scores_zero_per_component():
    si = ScoreInputs(direction="BULLISH", ema9_15=None, ema20_15=None, ema9_5=None, ema20_5=None, close=100, vwap=None,
                     atr=None, structure15=None, zone_confluence=None, chain=None, option=None, near_atm_gamma_max=None,
                     iv_median=None)
    sb = score(si, P)
    assert sb.total == 0 and len(sb.components) == 13
    assert all(not comp.data_ok for comp in sb.components if comp.key not in ("candle_formation",))


def test_setup_a_bullish_signal_generated_with_full_breakdown():
    r = evaluate(scenario(), P)
    assert r.signal is not None, r.messages
    s = r.signal
    assert s.setup == "A" and s.direction == "BULLISH" and s.state == SignalState.PENDING
    assert s.score["total"] >= P.min_score
    assert sum(cmp["max"] for cmp in s.score["components"]) == 100
    assert all(cmp["reasons"] for cmp in s.score["components"])
    assert s.strategy_version == STRATEGY_VERSION and s.snapshot_ref
    assert s.option["option_type"] == "CE"
    # entry only above confirmation-candle high
    conf = [x for x in scenario().c5][-1]
    assert s.plan["trigger"] > conf.high
    assert s.plan["stop"] == pytest.approx(round(conf.low - P.sl_atr_buffer * ind.atr(scenario().c5, 14)[-1], 2), abs=0.02)
    assert s.plan["t1"] - s.plan["trigger"] == pytest.approx(s.plan["risk_points"], abs=0.02)
    assert s.plan["t2"] - s.plan["trigger"] == pytest.approx(2 * s.plan["risk_points"], abs=0.02)
    assert s.premium and s.premium["premium_sl"] < s.premium["entry_premium_est"]


def test_setup_b_bearish_mirror():
    r = evaluate(scenario(mirror=True), P)
    assert r.candidate is not None, r.messages
    assert r.candidate["direction"] == "BEARISH"
    assert r.signal is not None and r.signal.setup == "B"
    assert r.signal.option["option_type"] == "PE" and r.signal.plan["trigger"] < r.candidate["plan"]["stop"]


def test_mandatory_overrides_score_stale_data():
    bad = DataQuality(False, ["Option chain is 120s old (> 30s)"])
    r = evaluate(scenario(quality=bad), P)
    assert r.signal is None and r.rejected is not None
    assert "data_quality" in r.rejected["reasons"]
    assert r.candidate["score"]["total"] >= P.min_score  # high score, still blocked


def test_mandatory_overrides_score_risk_gate():
    r = evaluate(scenario(risk_ok=False), P)
    assert r.signal is None and "risk_gate" in r.rejected["reasons"]


def test_min_score_enforced():
    strict = replace(P, min_score=99)
    r = evaluate(scenario(), strict)
    assert r.signal is None and "min_score" in r.rejected["reasons"]


def test_entry_cutoff_blocks():
    ctx = scenario()
    ctx.now = ctx.now.replace(hour=14, minute=50)
    ctx.chain.received_at = ctx.now
    r = evaluate(ctx, P)
    assert r.signal is None and "before_entry_cutoff" in r.rejected["reasons"]


def test_deterministic_and_reproducible():
    a = evaluate(scenario(), P)
    b = evaluate(scenario(), P)
    assert a.signal.to_dict() == b.signal.to_dict()
    assert a.signal.snapshot_ref == b.signal.snapshot_ref


def test_reward_risk_blocked_by_nearby_resistance():
    ctx = scenario()
    trig = evaluate(ctx, P).signal.plan["trigger"]
    # put the main call wall just above the trigger => insufficient room for 1:2
    for r in ctx.chain.rows:
        if r.option_type.value == "CE":
            r.oi = 100000.0
            r.prev_oi = 110000.0
    k = min(s for s in ctx.chain.strikes() if s > trig)
    ctx.chain.get(k, ctx.chain.rows[0].option_type.__class__("CE")).oi = 900000.0
    r = evaluate(ctx, P)
    assert r.signal is None and "reward_risk_to_next_sr" in r.rejected["reasons"]


def test_premium_plan_nonlinear():
    opt = {"delta": 0.5, "gamma": 0.002, "theta": -6, "vega": 10, "ask": 100.1, "bid": 99.9}
    pp = premium_plan(opt, 40, P)
    linear_loss = 0.5 * 40
    assert pp["components"]["gamma_relief"] == pytest.approx(0.5 * 0.002 * 1600)
    assert pp["premium_risk_per_unit"] != pytest.approx(linear_loss)
    assert premium_plan({**opt, "delta": None}, 40, P) is None


# ---------------- signal lifecycle ----------------
def test_signal_expiry_after_three_completed_candles_and_dedupe():
    ctx = scenario()
    s = evaluate(ctx, P).signal
    book = SignalBook()
    assert book.add(s) and not book.add(evaluate(ctx, P).signal)   # duplicate id rejected
    assert s.expires_at == s.confirm_ts + timedelta(minutes=20)
    t = s.confirm_ts + timedelta(minutes=5)
    for n in range(1, 4):
        t += timedelta(minutes=5)
        book.on_candle_close("NIFTY", t, lambda sig: (True, ""))
        if n < 3:
            assert s.state == SignalState.PENDING
    assert s.state == SignalState.EXPIRED and s.candles_seen == 3
    assert s.history[-1]["to"] == "EXPIRED"


def test_signal_trigger_and_invalidation():
    ctx = scenario()
    s = evaluate(ctx, P).signal
    book = SignalBook()
    book.add(s)
    t = s.confirm_ts + timedelta(minutes=6)
    assert book.on_price("NIFTY", s.plan["trigger"] - 1, t) == []
    fired = book.on_price("NIFTY", s.plan["trigger"] + 0.5, t)
    assert fired and s.state == SignalState.TRIGGERED
    with pytest.raises(ValueError):
        s.transition(SignalState.EXPIRED, t, "illegal")
    s2 = evaluate(ctx, P).signal
    b2 = SignalBook()
    b2.add(s2)
    b2.on_candle_close("NIFTY", s2.confirm_ts + timedelta(minutes=10), lambda sig: (False, "structure"))
    assert s2.state == SignalState.CANCELLED


def test_still_valid_cancels_on_close_below_stop():
    ctx = scenario()
    s = evaluate(ctx, P).signal
    last = ctx.c5[-1]
    nxt = c(last.ts + timedelta(minutes=5), last.close, last.close, s.plan["stop"] - 20, s.plan["stop"] - 10)
    ctx.c5 = ctx.c5 + [nxt]
    ok, why = still_valid(s, ctx, P)
    assert not ok and "beyond stop" in why


def test_setup_c_breakout_retest_detection():
    """Synthetic: level at 105 (resistance); breakout candle closes 107 on 2x volume; retest to 105.2; confirm."""
    from app.strategy.levels import Level
    d = ts(__import__("datetime").date(2026, 1, 6), 10, 0)
    cs = [c(d + timedelta(minutes=5 * k), 100, 104, 99, 103, v=1000) for k in range(25)]
    t = cs[-1].ts
    cs += [c(t + timedelta(minutes=5), 103, 107.5, 102.8, 107, v=3000),
           c(t + timedelta(minutes=10), 107, 107.5, 105.1, 106, v=900),
           c(t + timedelta(minutes=15), 106, 108, 105.6, 107.8, v=1500)]
    lvl = Level("PDH", "prev_day", 105, 105, "resistance", d, True, 2.0)
    vs = ind.sma([x.volume for x in cs], 20)
    br = find_breakout_retest(cs, len(cs) - 1, lambda k: [lvl], vs, 2.0, "BULLISH", P)
    assert br is not None and br.breakout_index == len(cs) - 3
    low_vol = [c(x.ts, x.open, x.high, x.low, x.close, v=1000) for x in cs]
    vs2 = ind.sma([x.volume for x in low_vol], 20)
    assert find_breakout_retest(low_vol, len(cs) - 1, lambda k: [lvl], vs2, 2.0, "BULLISH", P) is None
