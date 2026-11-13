"""Charges, sizing, limits, Secure Exit and exit management (SYNTHETIC values)."""
from datetime import date, datetime, time

import pytest

from app.core.types import IST
from app.risk import secure_exit as sx
from app.risk.charges import ChargeSchedule, leg_charges, round_trip_estimate
from app.risk.exits import ExitPlanState, on_price, on_candle_close
from app.risk.limits import DailyState, GateFlags, risk_gate, record_entry, record_exit
from app.risk.sizing import size_position
from app.strategy.params import StrategyParams

P = StrategyParams()
SCHED = ChargeSchedule()


def test_charges_effective_dated_stt():
    before = leg_charges("SELL", 100000, 1, date(2026, 3, 31), SCHED)
    after = leg_charges("SELL", 100000, 1, date(2026, 4, 1), SCHED)
    assert before.stt == pytest.approx(100.0) and after.stt == pytest.approx(150.0)
    assert after.rates_effective_from == "2026-04-01"


def test_charges_components_buy_leg():
    b = leg_charges("BUY", 100000, 1, date(2026, 11, 13), SCHED)
    assert b.stt == 0 and b.stamp == pytest.approx(3.0)
    assert b.exchange_txn == pytest.approx(35.03)
    assert b.sebi == pytest.approx(0.10)
    assert b.gst == pytest.approx(round((20 + 35.03 + 0.10) * 0.18, 2), abs=0.01)
    assert b.total == pytest.approx(b.brokerage + b.stamp + b.exchange_txn + b.sebi + b.gst, abs=0.02)


def test_schedule_roundtrip_serialization_and_missing_date():
    s2 = ChargeSchedule.from_dict(SCHED.to_dict())
    assert s2.rates_for(date(2026, 5, 1)).stt_sell_pct == 0.15
    with pytest.raises(ValueError):
        SCHED.rates_for(date(2020, 1, 1))


def test_sizing_respects_risk_and_funds():
    r = size_position(100000, 120.0, 100.0, 65, 15000, 15000, date(2026, 11, 13), SCHED)
    assert r.ok and r.qty == r.lots * 65
    assert r.max_loss <= 15000 and r.capital_required <= 100000
    # one more lot would breach a constraint
    r_more = size_position(100000, 120.0, 100.0, 65, 15000, 15000, date(2026, 11, 13), SCHED, max_lots=r.lots + 1)
    assert r_more.lots == r.lots
    small = size_position(5000, 120.0, 100.0, 65, 15000, 15000, date(2026, 11, 13), SCHED)
    assert not small.ok and "available balance" in small.reasons[0]
    tight = size_position(100000, 120.0, 100.0, 65, 1000, 15000, date(2026, 11, 13), SCHED)
    assert not tight.ok and "risk budget" in tight.reasons[0]
    daily_cap = size_position(100000, 120.0, 100.0, 65, 15000, 3000, date(2026, 11, 13), SCHED)
    assert daily_cap.max_loss <= 3000


def test_sizing_needs_verified_lot_size():
    r = size_position(100000, 120.0, 100.0, 0, 15000, 15000, date(2026, 11, 13), SCHED)
    assert not r.ok and "instrument master" in r.reasons[0]


def test_daily_limits_per_index_independent():
    now = datetime(2026, 11, 13, 10, 0, tzinfo=IST)
    ds = DailyState(now.date())
    f = GateFlags()
    assert risk_gate(ds, "NIFTY", now, P, f)["ok"]
    record_entry(ds, "NIFTY", "t1", "s1")
    g = risk_gate(ds, "NIFTY", now, P, f)
    assert not g["ok"] and "active position" in g["reasons"][0]
    assert risk_gate(ds, "BANKNIFTY", now, P, f)["ok"]
    record_exit(ds, "NIFTY", -500, -560)              # stopped-out entry still counts
    record_entry(ds, "NIFTY", "t2", "s2")
    record_exit(ds, "NIFTY", 100, 40)
    g2 = risk_gate(ds, "NIFTY", now, P, f)
    assert not g2["ok"] and "daily entry limit" in g2["reasons"][0]
    assert risk_gate(ds, "BANKNIFTY", now, P, f)["ok"]
    assert "already executed" in " ".join(risk_gate(ds, "BANKNIFTY", now, P, f, signal_id="s1")["reasons"])


def test_gate_blocks_kill_switch_uncertain_cutoff():
    ds = DailyState(date(2026, 11, 13))
    late = datetime(2026, 11, 13, 14, 50, tzinfo=IST)
    assert not risk_gate(ds, "NIFTY", late, P, GateFlags())["ok"]
    now = datetime(2026, 11, 13, 10, 0, tzinfo=IST)
    assert not risk_gate(ds, "NIFTY", now, P, GateFlags(kill_switch=True))["ok"]
    assert not risk_gate(ds, "NIFTY", now, P, GateFlags(order_state_uncertain=True))["ok"]
    assert not risk_gate(ds, "NIFTY", now, P, GateFlags(feed_fault="token expired"))["ok"]


# ---------------- Secure Exit ----------------
def test_secure_exit_stepped_monotonic_floor():
    cfg = sx.SecureExitConfig(profit_target_action="trail")
    st = sx.new_state("t", 100000, cfg)
    assert st.mode == "stepped"
    assert sx.update(st, 4000, cfg) is None and not st.active
    sx.update(st, 5200, cfg)
    assert st.active and st.floor == 0
    sx.update(st, 10500, cfg)
    assert st.floor == 5000
    sx.update(st, 9000, cfg)                     # pullback does not lower the floor
    assert st.floor == 5000
    sx.update(st, 15100, cfg)
    assert st.floor == 10000
    r = sx.update(st, 9999, cfg)
    assert r and r.startswith("SECURE_FLOOR") and st.floor == 10000


def test_secure_exit_profit_target_default_exit():
    cfg = sx.SecureExitConfig()
    st = sx.new_state("t", 100000, cfg)
    assert sx.update(st, 10000, cfg).startswith("SECURE_PROFIT_TARGET")


def test_secure_exit_small_capital_continuous_trail():
    cfg = sx.SecureExitConfig()
    st = sx.new_state("t", 20000, cfg)
    assert st.mode == "continuous"
    sx.update(st, 400, cfg)
    assert not st.active
    sx.update(st, 800, cfg)
    assert st.floor == 300
    sx.update(st, 1200, cfg)
    assert st.floor == 700
    sx.update(st, 900, cfg)
    assert st.floor == 700
    assert sx.update(st, 690, cfg).startswith("SECURE_FLOOR")


def test_secure_exit_max_loss_and_daily():
    cfg = sx.SecureExitConfig()
    st = sx.new_state("t", 100000, cfg)
    assert sx.update(st, -15000, cfg).startswith("SECURE_MAX_LOSS")
    assert sx.daily_breach(-10000, -5000, cfg) and not sx.daily_breach(-10000, -4999, cfg)


def test_secure_state_survives_serialization():
    cfg = sx.SecureExitConfig()
    st = sx.new_state("t", 100000, cfg)
    sx.update(st, 7000, cfg)
    st2 = sx.SecureExitState(**st.to_dict())
    assert st2.floor == st.floor and st2.peak_gross == 7000


# ---------------- exits ----------------
def mk_exit(lots=4):
    return ExitPlanState("BULLISH", 100.0, 90.0, 90.0, 110.0, 120.0, 130.0, premium_sl=50.0,
                         qty_total=lots * 65, qty_open=lots * 65, lot_size=65)


NOW = datetime(2026, 11, 13, 11, 0, tzinfo=IST)


def test_exit_stop_and_premium_stop():
    st = mk_exit()
    a = on_price(st, 89.9, 80, NOW, P, 0.5, 1.0)
    assert a[0].kind == "EXIT_ALL" and "technical stop" in a[0].reason
    st = mk_exit()
    a = on_price(st, 95, 49.9, NOW, P, 0.5, 1.0)
    assert a[0].kind == "EXIT_ALL" and "premium stop" in a[0].reason


def test_t1_books_half_moves_stop_and_trails_monotonic():
    st = mk_exit(4)
    a = on_price(st, 110.0, 90, NOW, P, 0.5, 1.0)
    assert a[0].kind == "PARTIAL_T1" and a[0].qty == 2 * 65
    assert st.stop == pytest.approx(102.0)          # breakeven + cost/|delta| = 100 + 1/0.5
    on_candle_close(st, 115, 113.0, 2.0, False, NOW, P)
    assert st.stop == pytest.approx(111.0)
    on_candle_close(st, 112, 108.0, 2.0, False, NOW, P)  # trail would loosen -> ignored
    assert st.stop == pytest.approx(111.0)
    st.qty_open = 2 * 65
    a = on_price(st, 130.0, 90, NOW, P, 0.5, 1.0)
    assert a[0].kind == "EXIT_ALL" and "T3" in a[0].reason


def test_single_lot_t1_exit_all_and_eod_and_structure():
    st = mk_exit(1)
    a = on_price(st, 110.0, 90, NOW, P, 0.5, 1.0)
    assert a[0].kind == "EXIT_ALL" and "1 lot" in a[0].reason
    st = mk_exit()
    a = on_price(st, 101, 90, NOW.replace(hour=15, minute=15), P, 0.5, 1.0)
    assert "EOD" in a[0].reason
    st = mk_exit()
    assert "structure" in on_candle_close(st, 101, 100, 2, True, NOW, P)[0].reason
