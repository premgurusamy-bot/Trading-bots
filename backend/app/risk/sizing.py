"""Position sizing from premium risk, lot size (from the verified instrument master), funds,
charges and slippage. Never exceeds available funds or the approved per-trade/daily risk.
Never scales up after losses (no inputs here depend on the previous trade result)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from typing import Optional

from app.risk.charges import ChargeSchedule, round_trip_estimate


@dataclass
class SizeResult:
    ok: bool
    lots: int
    qty: int
    lot_size: int
    entry_premium: float
    premium_sl: float
    capital_required: float
    est_charges: float
    max_loss: float              # (entry - premium SL) * qty + round-trip charges
    risk_budget: float
    reward_t1: Optional[float]
    rr_t1: Optional[float]
    reasons: list[str]

    def to_dict(self):
        return asdict(self)


def size_position(available_balance: float, entry_premium: float, premium_sl: float, lot_size: int,
                  per_trade_risk_cap: float, daily_loss_remaining: float, on: date, sched: ChargeSchedule,
                  capital_buffer_pct: float = 2.0, max_lots: Optional[int] = None,
                  t1_premium_est: Optional[float] = None) -> SizeResult:
    reasons: list[str] = []
    if lot_size is None or lot_size <= 0:
        return SizeResult(False, 0, 0, 0, entry_premium, premium_sl, 0, 0, 0, 0, None, None,
                          ["Lot size unavailable from instrument master"])
    if entry_premium <= 0 or premium_sl <= 0 or premium_sl >= entry_premium:
        return SizeResult(False, 0, 0, lot_size, entry_premium, premium_sl, 0, 0, 0, 0, None, None,
                          ["Invalid premium entry/stop"])
    budget = max(0.0, min(per_trade_risk_cap, daily_loss_remaining))
    per_unit = entry_premium - premium_sl
    lots = 0
    best = None
    k = 1
    while True:
        if max_lots is not None and k > max_lots:
            break
        qty = k * lot_size
        ch = round_trip_estimate(entry_premium, premium_sl, qty, on, sched)
        loss = per_unit * qty + ch
        cap = entry_premium * qty * (1 + capital_buffer_pct / 100) + ch
        if loss > budget or cap > available_balance:
            break
        lots, best = k, (qty, ch, loss, cap)
        k += 1
        if k > 10000:
            break
    if lots == 0:
        qty = lot_size
        ch = round_trip_estimate(entry_premium, premium_sl, qty, on, sched)
        loss = per_unit * qty + ch
        cap = entry_premium * qty * (1 + capital_buffer_pct / 100) + ch
        if cap > available_balance:
            reasons.append(f"1 lot needs ₹{cap:,.0f} but available balance is ₹{available_balance:,.0f}")
        if loss > budget:
            reasons.append(f"1 lot max loss ₹{loss:,.0f} exceeds risk budget ₹{budget:,.0f}")
        return SizeResult(False, 0, 0, lot_size, entry_premium, premium_sl, round(cap, 2), ch, round(loss, 2), budget,
                          None, None, reasons)
    qty, ch, loss, cap = best
    reward = ((t1_premium_est - entry_premium) * qty - ch) if t1_premium_est else None
    rr = (reward / loss) if (reward is not None and loss > 0) else None
    return SizeResult(True, lots, qty, lot_size, entry_premium, premium_sl, round(cap, 2), ch, round(loss, 2), budget,
                      round(reward, 2) if reward is not None else None, round(rr, 2) if rr is not None else None, [])
