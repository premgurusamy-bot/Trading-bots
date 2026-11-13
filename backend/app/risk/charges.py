"""Effective-dated, configurable charge schedule for NSE index OPTIONS (buyer side round trip).

The rates below are a maintained default schedule, NOT authoritative. Check them against the
current NSE circulars, Income-tax (STT) notifications and your Dhan pricing page, then update
them in Settings → Charges (each change is audited). Every computed trade stores the schedule
version it used.

Default schedule (researched 2026-11-13; see docs/CHARGES.md for sources):
* Brokerage (Dhan F&O): flat ₹20 per executed order.            [VERIFY on dhan.co/pricing]
* STT (options, SELL side, on premium): 0.10% until 2026-03-31; 0.15% from 2026-04-01
  (Union Budget 2026-27).                                         [VERIFY]
* NSE exchange transaction charge (options, on premium): 0.03503%.  [VERIFY NSE circular]
* SEBI turnover fee: ₹10 per crore = 0.0001% of premium turnover.
* Stamp duty: 0.003% of premium, BUY side only.
* GST: 18% on (brokerage + exchange transaction charge + SEBI fee).
* IPFT: default 0 (configurable).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class ChargeRates:
    effective_from: date
    brokerage_per_order: float = 20.0
    stt_sell_pct: float = 0.10
    exchange_txn_pct: float = 0.03503
    sebi_per_crore: float = 10.0
    stamp_buy_pct: float = 0.003
    gst_pct: float = 18.0
    ipft_per_crore: float = 0.0
    note: str = ""

    def to_dict(self):
        d = asdict(self)
        d["effective_from"] = self.effective_from.isoformat()
        return d


DEFAULT_SCHEDULE: list[ChargeRates] = [
    ChargeRates(date(2024, 10, 1), stt_sell_pct=0.10, note="STT 0.1% (Budget 2024); NSE txn 0.03503% from 2024-10-01"),
    ChargeRates(date(2026, 4, 1), stt_sell_pct=0.15, note="STT on options raised to 0.15% (Budget 2026-27) — VERIFY"),
]


@dataclass
class ChargeSchedule:
    entries: list[ChargeRates] = field(default_factory=lambda: list(DEFAULT_SCHEDULE))
    version: str = "default-2026-11-13"

    def rates_for(self, d: date) -> ChargeRates:
        valid = [e for e in self.entries if e.effective_from <= d]
        if not valid:
            raise ValueError(f"No charge schedule effective on {d}")
        return max(valid, key=lambda e: e.effective_from)

    def to_dict(self):
        return {"version": self.version, "entries": [e.to_dict() for e in self.entries]}

    @classmethod
    def from_dict(cls, d: dict) -> "ChargeSchedule":
        entries = []
        for e in d["entries"]:
            e = dict(e)
            e["effective_from"] = date.fromisoformat(e["effective_from"])
            entries.append(ChargeRates(**e))
        return cls(entries, d.get("version", "custom"))


@dataclass
class ChargeBreakdown:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi: float
    stamp: float
    gst: float
    ipft: float
    total: float
    rates_effective_from: str
    schedule_version: str

    def to_dict(self):
        return asdict(self)


def leg_charges(side: str, premium_turnover: float, orders: int, on: date, sched: ChargeSchedule) -> ChargeBreakdown:
    """Charges for one leg. `premium_turnover` = price * quantity (rupees); `orders` = executed orders."""
    r = sched.rates_for(on)
    brokerage = r.brokerage_per_order * orders
    stt = premium_turnover * r.stt_sell_pct / 100 if side == "SELL" else 0.0
    txn = premium_turnover * r.exchange_txn_pct / 100
    sebi = premium_turnover * r.sebi_per_crore / 1e7
    stamp = premium_turnover * r.stamp_buy_pct / 100 if side == "BUY" else 0.0
    ipft = premium_turnover * r.ipft_per_crore / 1e7
    gst = (brokerage + txn + sebi) * r.gst_pct / 100
    total = brokerage + stt + txn + sebi + stamp + gst + ipft
    rnd = lambda x: round(x, 2)
    return ChargeBreakdown(rnd(brokerage), rnd(stt), rnd(txn), rnd(sebi), rnd(stamp), rnd(gst), rnd(ipft), rnd(total),
                           r.effective_from.isoformat(), sched.version)


def round_trip_estimate(entry_premium: float, exit_premium: float, qty: int, on: date, sched: ChargeSchedule,
                        entry_orders: int = 1, exit_orders: int = 2) -> float:
    """Conservative estimate: exit assumed in 2 orders (50% at T1 + remainder)."""
    b = leg_charges("BUY", entry_premium * qty, entry_orders, on, sched)
    s = leg_charges("SELL", exit_premium * qty, exit_orders, on, sched)
    return round(b.total + s.total, 2)
