"""Paper broker + TradingService end-to-end on SYNTHETIC replay data:
order lifecycle, partial fills, exit confirmation, persistence/restart recovery, idempotency,
live-disabled default."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, func

from app import db as dbm
from app.core.service import TradingService
from app.core.types import Tick, OptionQuote, OptionType
from app.data.provider import ReplayProvider, InstrumentInfo
from app.execution.broker_base import OrderRequest, OrderStatus
from app.execution.paper import PaperBroker
from app.strategy import indicators as ind
from app.strategy.candles import resample
from app.strategy.signals import SignalState
from tests.synthetic import bullish_setup_a, chain_for


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def adv(self, s):
        self.t += timedelta(seconds=s)


@pytest.fixture
def dbsession(tmp_path):
    url = f"sqlite:///{(tmp_path / 't.db').as_posix()}"
    dbm.run_migrations(url)       # exercises the Alembic migration
    eng = dbm.make_engine(url)
    return dbm.make_session_factory(eng), url


def setup_world(dbsession, capital=100000.0):
    Session, _ = dbsession
    c5, c15, dp, conf = bullish_setup_a()
    t0 = conf.ts + timedelta(minutes=5, seconds=10)
    clock = Clock(t0)
    prov = ReplayProvider()
    prov.set_candles("NIFTY", 5, c5)
    prov.set_candles("NIFTY", 15, c15)
    prov.set_daily_prev("NIFTY", dp)
    e20 = ind.ema([x.close for x in c5], 20)[-1]
    spot = conf.close
    base = chain_for(spot, conf.ts.date(), put_wall=round(e20 / 50) * 50, call_wall=round(spot / 50) * 50 + 400,
                     received_at=t0 - timedelta(seconds=3), mp_strike=round(spot / 50) * 50 + 50)
    prov.push_chain("NIFTY", base)
    for r in base.rows:
        prov.set_instrument(InstrumentInfo(r.security_id, "NSE_FNO", f"NIFTY-{int(r.strike)}{r.option_type.value}",
                                           base.expiry, r.strike, r.option_type.value, 65, 0.05))
    prov.set_tick("NIFTY", Tick("NIFTY", spot, t0, t0, "SYNTHETIC"))
    svc = TradingService(prov, Session, paper_capital=capital, clock=clock)
    svc.load_state()
    return svc, prov, clock, base


def push(prov, base, clock, spot, prem_shift=0.0):
    ch = copy.deepcopy(base)
    ch.received_at = ch.feed_ts = clock()
    ch.underlying_ltp = spot
    for r in ch.rows:
        sgn = 1 if r.option_type == OptionType.CE else -1
        r.ltp = max(1.0, r.ltp + sgn * prem_shift)
        r.bid, r.ask = r.ltp - 0.1, r.ltp + 0.1
    prov.push_chain("NIFTY", ch)
    prov.set_tick("NIFTY", Tick("NIFTY", spot, clock(), clock(), "SYNTHETIC"))


def run_to_open_trade(dbsession):
    svc, prov, clock, base = setup_world(dbsession)
    svc.step()
    sig = svc.book.pending("NIFTY")[0]
    clock.adv(20)
    push(prov, base, clock, sig.plan["trigger"] + 0.5)
    svc.step()                                   # trigger -> entry order placed
    assert sig.state == SignalState.TRIGGERED
    clock.adv(1)
    push(prov, base, clock, sig.plan["trigger"] + 0.6)
    svc.step()                                   # fill on a fresh quote after latency
    return svc, prov, clock, base, sig


def test_paper_is_default_and_live_disabled(dbsession):
    svc, *_ = setup_world(dbsession)
    assert svc.mode == "PAPER" and not svc.live_enabled and svc.live_broker is None
    from app.config import AppSettings
    assert AppSettings(_env_file=None).live_trading_feature_flag is False


def test_paper_broker_partial_fills_latency_and_reject():
    now = datetime(2026, 11, 13, 10, 0, tzinfo=__import__("app.core.types", fromlist=["IST"]).IST)
    q = {"t": None, "q": None}
    b = PaperBroker(lambda sid: (q["q"], q["t"]) if q["q"] else None, 100000, latency_s=0.5)
    o = b.place(OrderRequest("c1", "T", "BUY", "X", "NSE_FNO", 130), now)
    assert b.place(OrderRequest("c1", "T", "BUY", "X", "NSE_FNO", 130), now) is o   # idempotent
    q["q"] = OptionQuote(100, OptionType.CE, ltp=100, bid=99.9, ask=100.1, ask_qty=65, bid_qty=65)
    q["t"] = now
    b.refresh(o, now + timedelta(seconds=1))
    assert o.filled_qty == 0                     # quote predates placement+latency
    q["t"] = now + timedelta(seconds=1)
    b.refresh(o, now + timedelta(seconds=1))
    assert o.status == OrderStatus.PARTIALLY_FILLED and o.filled_qty == 65
    assert o.avg_price == pytest.approx(100.2)   # ask + 2 ticks, not trigger price
    b.refresh(o, now + timedelta(seconds=1))
    assert o.filled_qty == 65                    # same quote is not re-used
    q["t"] = now + timedelta(seconds=2)
    b.refresh(o, now + timedelta(seconds=2))
    assert o.status == OrderStatus.FILLED and o.filled_qty == 130
    o2 = b.place(OrderRequest("c2", "T", "SELL", "Y", "NSE_FNO", 65), now)
    b.refresh(o2, now + timedelta(seconds=11))
    assert o2.status == OrderStatus.REJECTED


def test_end_to_end_signal_entry_partial_exit_and_close(dbsession):
    Session, _ = dbsession
    svc, prov, clock, base, sig = run_to_open_trade(dbsession)
    assert sig.state == SignalState.EXECUTED
    t = next(iter(svc.trades.values()))
    assert t.state == "OPEN" and t.filled_qty > 0 and svc.daily.entries["NIFTY"] == 1
    lots = t.filled_qty // 65
    # T1: underlying reaches T1, premium up
    clock.adv(5)
    push(prov, base, clock, t.exit_state.t1 + 0.5, prem_shift=5)
    svc.step()
    clock.adv(1)
    push(prov, base, clock, t.exit_state.t1 + 0.6, prem_shift=5)
    svc.step()
    if lots >= 2:
        assert t.exit_state.t1_done and t.exit_state.qty_open == t.filled_qty - (lots // 2) * 65
        assert t.exit_state.stop > t.exit_state.initial_stop       # moved toward breakeven
        # stop at breakeven: underlying falls back
        clock.adv(5)
        push(prov, base, clock, t.exit_state.stop - 1, prem_shift=1)
        svc.step()
        clock.adv(1)
        push(prov, base, clock, t.exit_state.stop - 1, prem_shift=1)
        svc.step()
    with Session() as s:
        row = s.get(dbm.TradeRow, t.id)
        assert row.state == "CLOSED" and row.open_qty == 0
        assert row.charges > 0 and row.net_pnl == pytest.approx(row.gross_pnl - row.charges, abs=0.02)
        assert "explanation" in row.journal["outcome"]
        nfills = s.execute(select(func.count()).select_from(dbm.FillRow)).scalar()
        assert nfills >= 2
        assert s.get(dbm.SignalRow, sig.id).state == "EXECUTED"
    assert svc.daily.active["NIFTY"] is None and svc.daily.entries["NIFTY"] == 1


def test_restart_recovery_restores_open_trade_counters_and_floor(dbsession):
    Session, _ = dbsession
    svc, prov, clock, base, sig = run_to_open_trade(dbsession)
    t = next(iter(svc.trades.values()))
    t.secure.floor, t.secure.active, t.secure.peak_gross = 1234.0, True, 6000.0
    svc._save_trade(t)
    svc2 = TradingService(prov, Session, clock=clock)
    svc2.load_state()
    assert t.id in svc2.trades
    t2 = svc2.trades[t.id]
    assert t2.state == "OPEN" and t2.exit_state.qty_open == t.exit_state.qty_open
    assert t2.secure.floor == 1234.0 and t2.secure.active
    assert svc2.daily.entries["NIFTY"] == 1 and svc2.daily.active["NIFTY"] == t.id
    assert svc2.book.signals[sig.id].state == SignalState.EXECUTED
    assert svc2.broker.available_balance() == pytest.approx(svc.broker.available_balance())
    # the same signal cannot be executed again (no duplicate execution / re-entry)
    from app.risk.limits import risk_gate
    assert not risk_gate(svc2.daily, "NIFTY", clock(), svc2.p, svc2.flags, signal_id=sig.id)["ok"]


def test_duplicate_evaluation_does_not_duplicate_signal(dbsession):
    Session, _ = dbsession
    svc, prov, clock, base = setup_world(dbsession)
    svc.step()
    svc.last_candle["NIFTY"] = None
    svc.step()                                   # same candle evaluated again
    with Session() as s:
        assert s.execute(select(func.count()).select_from(dbm.SignalRow)).scalar() == 1


def test_stale_tick_never_triggers(dbsession):
    svc, prov, clock, base = setup_world(dbsession)
    svc.step()
    sig = svc.book.pending("NIFTY")[0]
    old = clock() - timedelta(seconds=30)
    prov.set_tick("NIFTY", Tick("NIFTY", sig.plan["trigger"] + 5, old, old, "SYNTHETIC"))
    clock.adv(5)
    svc.step()
    assert sig.state == SignalState.PENDING


def test_entry_rejected_when_no_fresh_quote_cancels_signal_without_counting(dbsession):
    svc, prov, clock, base = setup_world(dbsession)
    svc.step()
    sig = svc.book.pending("NIFTY")[0]
    clock.adv(20)
    push(prov, base, clock, sig.plan["trigger"] + 0.5)
    svc.step()
    clock.adv(15)                                 # no new quotes -> paper reject after 10s
    prov.set_tick("NIFTY", Tick("NIFTY", sig.plan["trigger"] + 0.5, clock(), clock(), "SYNTHETIC"))
    svc.step()
    assert sig.state == SignalState.CANCELLED
    assert svc.daily.entries["NIFTY"] == 0 and svc.daily.active["NIFTY"] is None


def test_kill_switch_blocks_and_exits(dbsession):
    svc, prov, clock, base, sig = run_to_open_trade(dbsession)
    t = next(iter(svc.trades.values()))
    svc.kill_switch("test")
    assert t.state == "CLOSING" and svc.flags.kill_switch
    clock.adv(1)
    push(prov, base, clock, sig.plan["trigger"])
    svc.step()
    assert t.id not in svc.trades


def test_live_mode_requires_confirmation_and_is_not_reachable_by_default(dbsession):
    svc, prov, clock, base = setup_world(dbsession)
    with pytest.raises(RuntimeError):
        svc.mode = "LIVE"
        svc._active_broker()
    svc.mode = "PAPER"


def test_paper_reset_requires_confirmation_and_audits(dbsession):
    Session, _ = dbsession
    svc, *_ = setup_world(dbsession)
    ok, _ = svc.reset_paper("tester", "yes")
    assert not ok
    ok, _ = svc.reset_paper("tester", "RESET PAPER ACCOUNT", 50000)
    assert ok and svc.broker.available_balance() == 50000
    with Session() as s:
        assert s.execute(select(dbm.AuditLog).where(dbm.AuditLog.action == "paper_reset")).first()
