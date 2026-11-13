"""Broker abstraction shared by the paper broker and the (disabled-by-default) Dhan live broker.

A position or exit is only considered done when the BROKER reports fills (broker-confirmed state),
never because an HTTP request returned 200.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderStatus(str, Enum):
    NEW = "NEW"                       # created locally, not yet acknowledged
    OPEN = "OPEN"                     # acknowledged by broker, working
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"               # could not confirm => stop new entries & reconcile


FINAL = {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED}


@dataclass
class OrderRequest:
    client_order_id: str              # idempotency key
    trade_id: str
    side: str                         # BUY | SELL
    security_id: str
    exchange_segment: str             # NSE_FNO
    qty: int
    order_type: str = "MARKET"        # MARKET | LIMIT
    limit_price: Optional[float] = None
    purpose: str = "ENTRY"            # ENTRY | EXIT_PARTIAL | EXIT_ALL | EOD


@dataclass
class Fill:
    fill_id: str
    qty: int
    price: float
    ts: datetime


@dataclass
class BrokerOrder:
    request: OrderRequest
    status: OrderStatus
    broker_order_id: Optional[str] = None
    filled_qty: int = 0
    avg_price: Optional[float] = None
    fills: list[Fill] = field(default_factory=list)
    reason: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    raw: Optional[dict] = None

    @property
    def remaining(self) -> int:
        return self.request.qty - self.filled_qty

    def add_fill(self, f: Fill):
        if any(x.fill_id == f.fill_id for x in self.fills):
            return  # duplicate fill message
        total = (self.avg_price or 0) * self.filled_qty + f.price * f.qty
        self.fills.append(f)
        self.filled_qty += f.qty
        self.avg_price = round(total / self.filled_qty, 4)
        self.status = OrderStatus.FILLED if self.filled_qty >= self.request.qty else OrderStatus.PARTIALLY_FILLED

    def to_dict(self):
        d = asdict(self)
        d["status"] = self.status.value
        for f in d["fills"]:
            f["ts"] = f["ts"].isoformat()
        for k in ("created_at", "updated_at"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


class Broker(ABC):
    mode: str = "PAPER"

    @abstractmethod
    def place(self, req: OrderRequest, now: datetime) -> BrokerOrder: ...

    @abstractmethod
    def refresh(self, order: BrokerOrder, now: datetime) -> BrokerOrder: ...

    @abstractmethod
    def cancel(self, order: BrokerOrder, now: datetime) -> BrokerOrder: ...

    @abstractmethod
    def available_balance(self) -> Optional[float]: ...
