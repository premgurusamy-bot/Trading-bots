"""Per-index daily counters and the pre-entry risk gate.

* NIFTY and BANK NIFTY are tracked separately: max 2 entries each per trading day.
* Max 1 active position per index.
* A stopped-out entry counts as a trade (the counter increments at ENTRY fill, never decrements).
* No automatic re-entry after exit (each signal id can be executed at most once).
* Daily stop (Secure-Exit max daily loss), entry cutoff, kill switch and uncertain-order states block entries.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time
from typing import Optional

from app.strategy.params import StrategyParams


@dataclass
class DailyState:
    day: date
    entries: dict = field(default_factory=lambda: {"NIFTY": 0, "BANKNIFTY": 0})
    active: dict = field(default_factory=lambda: {"NIFTY": None, "BANKNIFTY": None})   # trade id or None
    executed_signals: set = field(default_factory=set)
    realised_gross: float = 0.0
    realised_net: float = 0.0
    blocked_reason: Optional[str] = None

    def to_dict(self):
        d = asdict(self)
        d["day"] = self.day.isoformat()
        d["executed_signals"] = sorted(self.executed_signals)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DailyState":
        return cls(date.fromisoformat(d["day"]), dict(d["entries"]), dict(d["active"]), set(d.get("executed_signals", [])),
                   d.get("realised_gross", 0.0), d.get("realised_net", 0.0), d.get("blocked_reason"))


@dataclass
class GateFlags:
    kill_switch: bool = False
    order_state_uncertain: bool = False
    feed_fault: Optional[str] = None


def risk_gate(ds: DailyState, index: str, now: datetime, p: StrategyParams, flags: GateFlags,
              signal_id: Optional[str] = None) -> dict:
    r = []
    if ds.day != now.date():
        r.append("Daily state not rolled over for today")
    if ds.entries.get(index, 0) >= p.max_entries_per_index_per_day:
        r.append(f"{index}: daily entry limit {p.max_entries_per_index_per_day} reached")
    if ds.active.get(index):
        r.append(f"{index}: an active position already exists")
    if ds.blocked_reason:
        r.append(f"Trading blocked: {ds.blocked_reason}")
    if now.time() > p.entry_cutoff:
        r.append(f"After entry cutoff {p.entry_cutoff.strftime('%H:%M')}")
    if now.time() < p.session_open:
        r.append("Before session open")
    if flags.kill_switch:
        r.append("Kill switch engaged")
    if flags.order_state_uncertain:
        r.append("Order state uncertain — reconcile with broker before new entries")
    if flags.feed_fault:
        r.append(f"Feed fault: {flags.feed_fault}")
    if signal_id and signal_id in ds.executed_signals:
        r.append("Signal already executed (no duplicate execution / re-entry)")
    return {"ok": not r, "reasons": r}


def record_entry(ds: DailyState, index: str, trade_id: str, signal_id: str) -> None:
    ds.entries[index] = ds.entries.get(index, 0) + 1
    ds.active[index] = trade_id
    ds.executed_signals.add(signal_id)


def record_exit(ds: DailyState, index: str, gross: float, net: float) -> None:
    ds.active[index] = None
    ds.realised_gross += gross
    ds.realised_net += net
