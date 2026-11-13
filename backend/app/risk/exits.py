"""Exit management for an open option position.

Rules (Strategy V1.0):
* Exit on whichever comes first: underlying LTP crosses the technical stop, or the option BID falls
  to the premium stop (premium SL controls rupee loss; technical SL defines setup invalidation).
* T1 (1R): book 50% (whole lots, rounded down). With only 1 lot: `single_lot_t1_action` ("exit_all").
* After T1: stop moves toward breakeven + costs (in underlying points: cost per unit / |delta|);
  the remainder trails on each completed 5m candle at EMA9 -/+ trail_atr_mult*ATR. The stop is
  monotonic (it only tightens).
* Remainder target: T3 if available, else T2.
* Exit if market structure invalidates the setup (opposing confirmed structure break on 5m).
* Square off at `squareoff_time`.
Every action carries a reason. Executions are simulated or broker-confirmed elsewhere: this module
only DECIDES.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from app.strategy.params import StrategyParams


@dataclass
class ExitPlanState:
    direction: str
    entry_underlying: float
    stop: float                       # current underlying stop (monotonic)
    initial_stop: float
    t1: float
    t2: float
    t3: Optional[float]
    premium_sl: float
    qty_total: int
    qty_open: int
    lot_size: int
    t1_done: bool = False
    breakeven_set: bool = False
    stop_history: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class ExitAction:
    kind: str           # "PARTIAL_T1" | "EXIT_ALL"
    qty: int
    reason: str


def _tighten(st: ExitPlanState, new_stop: float, at: str, why: str):
    bull = st.direction == "BULLISH"
    better = new_stop > st.stop if bull else new_stop < st.stop
    if better:
        st.stop_history.append({"at": at, "from": st.stop, "to": round(new_stop, 2), "why": why})
        st.stop = round(new_stop, 2)


def on_price(st: ExitPlanState, underlying: float, option_bid: Optional[float], now: datetime,
             p: StrategyParams, delta: Optional[float], cost_per_unit: float) -> list[ExitAction]:
    if st.qty_open <= 0:
        return []
    bull = st.direction == "BULLISH"
    acts: list[ExitAction] = []
    if now.time() >= p.squareoff_time:
        return [ExitAction("EXIT_ALL", st.qty_open, f"EOD square-off at {p.squareoff_time.strftime('%H:%M')}")]
    if (bull and underlying <= st.stop) or (not bull and underlying >= st.stop):
        tag = "trailing/breakeven stop" if st.t1_done else "technical stop"
        return [ExitAction("EXIT_ALL", st.qty_open, f"Underlying {underlying} hit {tag} {st.stop}")]
    if option_bid is not None and option_bid <= st.premium_sl:
        return [ExitAction("EXIT_ALL", st.qty_open, f"Option bid {option_bid} hit premium stop {st.premium_sl}")]
    final_t = st.t3 if st.t3 is not None else st.t2
    if st.t1_done and ((bull and underlying >= final_t) or (not bull and underlying <= final_t)):
        return [ExitAction("EXIT_ALL", st.qty_open, f"Final target {'T3' if st.t3 is not None else 'T2'} {final_t} reached")]
    if not st.t1_done and ((bull and underlying >= st.t1) or (not bull and underlying <= st.t1)):
        lots_total = st.qty_total // st.lot_size
        book_lots = int(lots_total * p.t1_book_fraction)
        st.t1_done = True
        if book_lots == 0:
            if p.single_lot_t1_action == "exit_all":
                return [ExitAction("EXIT_ALL", st.qty_open, f"T1 {st.t1} reached with 1 lot: exit all (50% booking impossible)")]
        else:
            acts.append(ExitAction("PARTIAL_T1", book_lots * st.lot_size, f"T1 {st.t1} reached: book {book_lots} of {lots_total} lots"))
        # move stop toward breakeven + costs
        if delta:
            be = st.entry_underlying + (cost_per_unit / abs(delta)) * (1 if bull else -1)
        else:
            be = st.entry_underlying
        _tighten(st, be, now.isoformat(), "breakeven + costs after T1")
        st.breakeven_set = True
    return acts


def on_candle_close(st: ExitPlanState, close: float, ema9: Optional[float], atr: Optional[float], structure_broken: bool,
                    now: datetime, p: StrategyParams) -> list[ExitAction]:
    if st.qty_open <= 0:
        return []
    if structure_broken:
        return [ExitAction("EXIT_ALL", st.qty_open, "Market structure invalidated the setup (opposing confirmed break)")]
    if st.t1_done and ema9 is not None and atr is not None:
        bull = st.direction == "BULLISH"
        trail = ema9 - p.trail_atr_mult * atr if bull else ema9 + p.trail_atr_mult * atr
        _tighten(st, trail, now.isoformat(), f"trail EMA9 {'-' if bull else '+'} {p.trail_atr_mult} ATR")
    return []
