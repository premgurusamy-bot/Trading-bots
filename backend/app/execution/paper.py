"""Paper broker — conservative, bid/ask-aware simulation. Places NO real orders.

Model (documented in docs/PAPER_TRADING.md):
* Latency: an order can fill only on a quote received at least `latency_s` after placement.
* BUY fills at ASK + slippage_ticks*tick; SELL at BID - slippage_ticks*tick (never the trigger price).
* Depth: each fresh quote fills at most the displayed ask_qty (BUY) / bid_qty (SELL); the remainder
  waits for the next fresh quote => partial fills.
* A quote is used at most once per order (no refilling against the same depth).
* LIMIT BUY fills only if ask <= limit; LIMIT SELL only if bid >= limit.
* Rejection: no two-sided quote / stale quote for `reject_after_s`, qty <= 0, or insufficient
  paper funds for a BUY.
* Funds: starting paper capital minus premium paid plus premium received (charges are tracked on the
  trade and deducted from the balance at trade close).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional

from app.core.types import OptionQuote
from app.execution.broker_base import Broker, BrokerOrder, OrderRequest, OrderStatus, Fill, FINAL

QuoteFn = Callable[[str], Optional[tuple[OptionQuote, datetime]]]


class PaperBroker(Broker):
    mode = "PAPER"

    def __init__(self, quote_fn: QuoteFn, capital: float, tick: float = 0.05, slippage_ticks: int = 2,
                 latency_s: float = 0.5, max_quote_age_s: float = 5.0, reject_after_s: float = 10.0):
        self.quote_fn = quote_fn
        self.cash = capital
        self.tick = tick
        self.slippage_ticks = slippage_ticks
        self.latency_s = latency_s
        self.max_quote_age_s = max_quote_age_s
        self.reject_after_s = reject_after_s
        self.orders: dict[str, BrokerOrder] = {}
        self._used_quotes: dict[str, set] = {}
        self._seq = 0

    def available_balance(self) -> Optional[float]:
        return round(self.cash, 2)

    def place(self, req: OrderRequest, now: datetime) -> BrokerOrder:
        if req.client_order_id in self.orders:          # idempotent
            return self.orders[req.client_order_id]
        self._seq += 1
        o = BrokerOrder(req, OrderStatus.OPEN, broker_order_id=f"PAPER-{self._seq:06d}", created_at=now, updated_at=now)
        if req.qty <= 0:
            o.status, o.reason = OrderStatus.REJECTED, "Quantity must be positive"
        if req.side == "BUY":
            q = self.quote_fn(req.security_id)
            if q and q[0].ask and q[0].ask * req.qty > self.cash:
                o.status, o.reason = OrderStatus.REJECTED, "Insufficient paper funds"
        self.orders[req.client_order_id] = o
        self._used_quotes[req.client_order_id] = set()
        return o

    def refresh(self, o: BrokerOrder, now: datetime) -> BrokerOrder:
        if o.status in FINAL:
            return o
        if (now - o.created_at).total_seconds() < self.latency_s:
            return o
        got = self.quote_fn(o.request.security_id)
        q, rx = (got if got else (None, None))
        fresh = q is not None and rx is not None and (now - rx).total_seconds() <= self.max_quote_age_s \
            and rx >= o.created_at + timedelta(seconds=self.latency_s)
        two_sided = q is not None and q.bid is not None and q.ask is not None and q.bid > 0 and q.ask >= q.bid
        if not (fresh and two_sided):
            if (now - o.created_at).total_seconds() >= self.reject_after_s and o.filled_qty == 0:
                o.status, o.reason, o.updated_at = OrderStatus.REJECTED, "No fresh two-sided quote (paper reject)", now
            return o
        key = rx.isoformat()
        if key in self._used_quotes[o.request.client_order_id]:
            return o
        slip = self.slippage_ticks * self.tick
        if o.request.side == "BUY":
            px = q.ask + slip
            if o.request.order_type == "LIMIT" and o.request.limit_price is not None and q.ask > o.request.limit_price:
                return o
            depth = int(q.ask_qty) if q.ask_qty else o.remaining
        else:
            px = max(q.bid - slip, self.tick)
            if o.request.order_type == "LIMIT" and o.request.limit_price is not None and q.bid < o.request.limit_price:
                return o
            depth = int(q.bid_qty) if q.bid_qty else o.remaining
        qty = min(o.remaining, max(depth, 0))
        if qty <= 0:
            return o
        self._used_quotes[o.request.client_order_id].add(key)
        px = round(round(px / self.tick) * self.tick, 2)
        o.add_fill(Fill(f"{o.request.client_order_id}-F{len(o.fills) + 1}", qty, px, now))
        o.updated_at = now
        self.cash += (-1 if o.request.side == "BUY" else 1) * px * qty
        return o

    def cancel(self, o: BrokerOrder, now: datetime) -> BrokerOrder:
        if o.status not in FINAL:
            o.status, o.reason, o.updated_at = OrderStatus.CANCELLED, (o.reason or "Cancelled"), now
        return o

    def deduct_charges(self, amount: float):
        self.cash -= amount
