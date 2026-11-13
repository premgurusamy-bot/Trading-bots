"""Secure-Exit / profit-protection layer — an ADDITIONAL risk layer, separate from the strategy's
technical SL, targets and daily trade limits (it never replaces them).

No earlier implementation existed in the repository (see docs/AUDIT.md), so the settings come
from the master specification. All thresholds are on GROSS P&L (before charges).

Settings (spec):
  activation_level   ₹5,000   second_level ₹10,000   profit_target ₹10,000
  trailing_increment ₹5,000   max_loss_per_trade ₹15,000   max_daily_loss ₹15,000
  small_capital_threshold ₹25,000: activation ₹500, continuous trailing distance ₹500

INTERPRETATION (documented in docs/SECURE_EXIT.md and flagged for user approval):
* Capital >= ₹25,000 (stepped floor):
    peak >= activation (5k)              -> floor = peak_step - increment, where
    peak_step = floor(peak / increment) * increment
    i.e. 5k peak -> floor ₹0 (breakeven gross), 10k (second level) -> floor ₹5k,
    15k -> ₹10k, ...
    profit_target (10k): action configurable. Default "exit" (book the whole position at target).
* Capital < ₹25,000 (continuous): once peak >= ₹500, floor = peak - ₹500 (continuous trail).
* Floor is MONOTONIC: it can only rise. Exit when gross P&L <= floor while protection is active.
* Exit when gross loss >= max_loss_per_trade.
* Daily: if realised + unrealised gross P&L <= -max_daily_loss, exit everything and block entries.
The state is persisted after every change and restored on restart.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass(frozen=True)
class SecureExitConfig:
    activation_level: float = 5000.0
    second_level: float = 10000.0
    profit_target: float = 10000.0
    trailing_increment: float = 5000.0
    max_loss_per_trade: float = 15000.0
    max_daily_loss: float = 15000.0
    small_capital_threshold: float = 25000.0
    small_activation: float = 500.0
    small_trail_distance: float = 500.0
    profit_target_action: str = "exit"      # "exit" | "trail"
    enabled: bool = True

    def to_dict(self):
        return asdict(self)


@dataclass
class SecureExitState:
    trade_id: str
    mode: str                       # "stepped" | "continuous"
    peak_gross: float = 0.0
    floor: Optional[float] = None   # None => protection not yet active
    active: bool = False
    last_gross: float = 0.0
    triggered: Optional[str] = None

    def to_dict(self):
        return asdict(self)


def new_state(trade_id: str, capital: float, cfg: SecureExitConfig) -> SecureExitState:
    return SecureExitState(trade_id, "continuous" if capital < cfg.small_capital_threshold else "stepped")


def update(state: SecureExitState, gross_pnl: float, cfg: SecureExitConfig) -> Optional[str]:
    """Feed the latest GROSS P&L of an open trade. Returns an exit reason or None. Floor never decreases."""
    if not cfg.enabled or state.triggered:
        return state.triggered
    state.last_gross = gross_pnl
    state.peak_gross = max(state.peak_gross, gross_pnl)
    if gross_pnl <= -cfg.max_loss_per_trade:
        state.triggered = f"SECURE_MAX_LOSS: gross {gross_pnl:.0f} <= -{cfg.max_loss_per_trade:.0f}"
        return state.triggered
    new_floor = None
    if state.mode == "continuous":
        if state.peak_gross >= cfg.small_activation:
            new_floor = state.peak_gross - cfg.small_trail_distance
    else:
        if state.peak_gross >= cfg.activation_level:
            step = math.floor(state.peak_gross / cfg.trailing_increment) * cfg.trailing_increment
            new_floor = max(0.0, step - cfg.trailing_increment)
    if new_floor is not None:
        state.active = True
        state.floor = new_floor if state.floor is None else max(state.floor, new_floor)
    if state.mode == "stepped" and cfg.profit_target_action == "exit" and gross_pnl >= cfg.profit_target:
        state.triggered = f"SECURE_PROFIT_TARGET: gross {gross_pnl:.0f} >= {cfg.profit_target:.0f}"
        return state.triggered
    if state.active and state.floor is not None and gross_pnl <= state.floor:
        state.triggered = f"SECURE_FLOOR: gross {gross_pnl:.0f} <= protected floor {state.floor:.0f}"
        return state.triggered
    return None


def daily_breach(realised_gross: float, unrealised_gross: float, cfg: SecureExitConfig) -> bool:
    return cfg.enabled and (realised_gross + unrealised_gross) <= -cfg.max_daily_loss
