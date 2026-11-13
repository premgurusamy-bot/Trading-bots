"""Signal lifecycle: PENDING -> TRIGGERED -> EXECUTED, or PENDING -> CANCELLED | EXPIRED.
Candidates that fail a mandatory condition are REJECTED and go to the research log only.

Expiry: a pending signal expires when the 3rd completed 5m candle AFTER the confirmation candle closes
without a trigger. Every transition is recorded with timestamp and reason. Signal IDs are deterministic
(idempotency keys), so re-evaluating the same candle cannot create a duplicate.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class SignalState(str, Enum):
    PENDING = "PENDING"
    TRIGGERED = "TRIGGERED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


TERMINAL = {SignalState.EXECUTED, SignalState.CANCELLED, SignalState.EXPIRED, SignalState.REJECTED}


def signal_id(index: str, setup: str, direction: str, confirm_ts: datetime) -> str:
    return f"{index}|{setup}|{direction}|{confirm_ts.isoformat()}"


@dataclass
class Signal:
    id: str
    index: str
    direction: str
    setup: str
    confirm_ts: datetime
    created_at: datetime
    expires_at: datetime                 # close time of the 3rd candle after confirmation
    plan: dict
    premium: Optional[dict]
    option: Optional[dict]
    score: dict
    mandatory: dict
    invalidation: list[str]
    strategy_version: str
    params_hash: str
    snapshot_ref: Optional[str] = None
    state: SignalState = SignalState.PENDING
    candles_seen: int = 0
    history: list[dict] = field(default_factory=list)
    trigger_ts: Optional[datetime] = None
    trigger_price: Optional[float] = None

    def transition(self, new: SignalState, at: datetime, reason: str) -> bool:
        if self.state in TERMINAL or self.state == new:
            return False
        allowed = {
            SignalState.PENDING: {SignalState.TRIGGERED, SignalState.CANCELLED, SignalState.EXPIRED},
            SignalState.TRIGGERED: {SignalState.EXECUTED, SignalState.CANCELLED},
        }
        if new not in allowed.get(self.state, set()):
            raise ValueError(f"Illegal transition {self.state} -> {new}")
        self.history.append({"from": self.state.value, "to": new.value, "at": at.isoformat(), "reason": reason})
        self.state = new
        return True

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("confirm_ts", "created_at", "expires_at", "trigger_ts"):
            d[k] = d[k].isoformat() if d[k] else None
        d["state"] = self.state.value
        return d


def expiry_time(confirm_ts: datetime, validity_candles: int, tf_minutes: int = 5) -> datetime:
    # confirmation candle opens at confirm_ts and closes at +5m; then `validity_candles` more candles
    return confirm_ts + timedelta(minutes=tf_minutes * (1 + validity_candles))


class SignalBook:
    """In-memory view of today's signals; the persistence layer mirrors every change."""

    def __init__(self):
        self.signals: dict[str, Signal] = {}

    def add(self, s: Signal) -> bool:
        if s.id in self.signals:
            return False
        self.signals[s.id] = s
        return True

    def pending(self, index: Optional[str] = None) -> list[Signal]:
        return [s for s in self.signals.values() if s.state == SignalState.PENDING and (index is None or s.index == index)]

    def on_candle_close(self, index: str, candle_close_ts: datetime, still_valid: callable) -> list[Signal]:
        """Call after each completed 5m candle. `still_valid(signal) -> (bool, reason)` re-checks
        structure/derivatives. Returns signals whose state changed."""
        changed = []
        for s in self.pending(index):
            if candle_close_ts <= s.confirm_ts + timedelta(minutes=5):
                continue
            s.candles_seen += 1
            ok, why = still_valid(s)
            if not ok:
                s.transition(SignalState.CANCELLED, candle_close_ts, f"Invalidated before entry: {why}")
                changed.append(s)
            elif candle_close_ts >= s.expires_at:
                s.transition(SignalState.EXPIRED, candle_close_ts,
                             f"No trigger within {s.candles_seen} completed 5m candles")
                changed.append(s)
        return changed

    def on_price(self, index: str, ltp: float, ts: datetime) -> list[Signal]:
        """Trigger check: break ABOVE the confirmation high (bull) / BELOW the low (bear)."""
        fired = []
        for s in self.pending(index):
            if ts < s.confirm_ts + timedelta(minutes=5) or ts >= s.expires_at:
                continue
            trig = s.plan["trigger"]
            hit = ltp >= trig if s.direction == "BULLISH" else ltp <= trig
            if hit:
                s.trigger_ts, s.trigger_price = ts, ltp
                s.transition(SignalState.TRIGGERED, ts, f"Price {ltp} crossed trigger {trig}")
                fired.append(s)
        return fired
