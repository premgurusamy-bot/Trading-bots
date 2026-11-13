"""Dhan LIVE broker — DISABLED BY DEFAULT.

Construction requires ALL of:
  1. LIVE_TRADING_FEATURE_FLAG=true in .env (settings.live_trading_feature_flag)
  2. an explicit `enable_token` produced by the UI enablement flow (risk acknowledgement)
  3. not running under pytest (PYTEST_CURRENT_TEST unset) — live orders cannot be activated in tests.

Order placement payload: VERIFIED field names from official SDK v2.3.0 `place_order`.
Order-status values and response fields (orderId, orderStatus, filledQty, averageTradedPrice,
availabelBalance) are from Dhan v2 docs as known to the implementer — UNVERIFIED here. Any
status not in STATUS_MAP becomes UNKNOWN, which blocks new entries until reconciled.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import Optional

from app.config import settings
from app.data.dhan.client import DhanClient, DhanError
from app.execution.broker_base import Broker, BrokerOrder, OrderRequest, OrderStatus, Fill, FINAL

STATUS_MAP = {
    "TRANSIT": OrderStatus.OPEN, "PENDING": OrderStatus.OPEN, "PART_TRADED": OrderStatus.PARTIALLY_FILLED,
    "TRADED": OrderStatus.FILLED, "REJECTED": OrderStatus.REJECTED, "CANCELLED": OrderStatus.CANCELLED,
    "EXPIRED": OrderStatus.CANCELLED,
}


class LiveTradingNotPermitted(RuntimeError):
    pass


def correlation_id(client_order_id: str) -> str:
    """Short deterministic correlation id (idempotency key on Dhan's side)."""
    return "NB" + hashlib.sha1(client_order_id.encode()).hexdigest()[:18]


class DhanLiveBroker(Broker):
    mode = "LIVE"

    def __init__(self, client: DhanClient, enable_token: Optional[str], expected_token: Optional[str],
                 product_type: str = "INTRADAY"):
        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise LiveTradingNotPermitted("Live trading cannot be activated during tests")
        if not settings.live_trading_feature_flag:
            raise LiveTradingNotPermitted("LIVE_TRADING_FEATURE_FLAG is not true")
        if not enable_token or enable_token != expected_token:
            raise LiveTradingNotPermitted("Live trading not enabled through the UI acknowledgement flow")
        self.client = client
        self.product_type = product_type
        self._balance: Optional[float] = None

    def available_balance(self) -> Optional[float]:
        try:
            d = self.client.fund_limit()
            v = d.get("availabelBalance", d.get("availableBalance")) if isinstance(d, dict) else None
            self._balance = float(v) if v is not None else None
        except DhanError:
            self._balance = None
        return self._balance

    def place(self, req: OrderRequest, now: datetime) -> BrokerOrder:
        cid = correlation_id(req.client_order_id)
        o = BrokerOrder(req, OrderStatus.NEW, created_at=now, updated_at=now)
        # idempotency: if the broker already knows this correlation id, adopt it instead of re-sending
        try:
            existing = self.client.order_by_correlation(cid)
            if isinstance(existing, dict) and existing.get("orderId"):
                return self._apply(o, existing, now)
        except DhanError as e:
            if e.kind in ("AUTH", "NETWORK", "RATE_LIMIT"):
                o.status, o.reason = OrderStatus.UNKNOWN, f"pre-check failed: {e.kind}"
                return o
        body = {
            "transactionType": req.side, "exchangeSegment": req.exchange_segment, "productType": self.product_type,
            "orderType": req.order_type, "validity": "DAY", "securityId": req.security_id, "quantity": int(req.qty),
            "disclosedQuantity": 0, "price": float(req.limit_price or 0), "afterMarketOrder": False,
            "triggerPrice": 0.0, "correlationId": cid, "dhanClientId": self.client._cid,
        }
        try:
            resp = self.client.place_order(body)
        except DhanError as e:
            # NETWORK errors are ambiguous: the order may or may not exist => UNKNOWN (reconcile)
            o.status = OrderStatus.UNKNOWN if e.kind in ("NETWORK",) else OrderStatus.REJECTED
            o.reason = str(e)
            return o
        o.broker_order_id = str(resp.get("orderId")) if isinstance(resp, dict) and resp.get("orderId") else None
        o.status = STATUS_MAP.get(str(resp.get("orderStatus", "")).upper(), OrderStatus.UNKNOWN) if isinstance(resp, dict) else OrderStatus.UNKNOWN
        o.raw = {k: resp.get(k) for k in ("orderId", "orderStatus")} if isinstance(resp, dict) else None
        return o

    def _apply(self, o: BrokerOrder, d: dict, now: datetime) -> BrokerOrder:
        o.broker_order_id = str(d.get("orderId"))
        st = STATUS_MAP.get(str(d.get("orderStatus", "")).upper(), OrderStatus.UNKNOWN)
        filled = int(d.get("filledQty") or 0)
        avg = d.get("averageTradedPrice")
        if filled > o.filled_qty and avg:
            # Dhan reports cumulative filled qty and average price; derive the incremental fill
            prev_val = (o.avg_price or 0) * o.filled_qty
            inc = filled - o.filled_qty
            px = (float(avg) * filled - prev_val) / inc
            o.add_fill(Fill(f"{o.broker_order_id}-{filled}", inc, round(px, 2), now))
        if st == OrderStatus.OPEN and o.filled_qty > 0:
            o.status = OrderStatus.PARTIALLY_FILLED
        elif st == OrderStatus.FILLED and o.filled_qty < o.request.qty:
            o.status = OrderStatus.UNKNOWN        # broker says filled but fills don't add up => reconcile
            o.reason = "Filled status without matching quantity"
        else:
            o.status = st
        o.reason = d.get("omsErrorDescription") or o.reason
        o.raw = {k: d.get(k) for k in ("orderId", "orderStatus", "filledQty", "averageTradedPrice")}
        o.updated_at = now
        return o

    def refresh(self, o: BrokerOrder, now: datetime) -> BrokerOrder:
        if o.status in FINAL:
            return o
        try:
            d = self.client.order_by_correlation(correlation_id(o.request.client_order_id))
        except DhanError as e:
            o.status = OrderStatus.UNKNOWN
            o.reason = f"status check failed: {e.kind}"
            return o
        if isinstance(d, list):
            d = d[0] if d else {}
        return self._apply(o, d, now) if d else o

    def cancel(self, o: BrokerOrder, now: datetime) -> BrokerOrder:
        if o.broker_order_id and o.status not in FINAL:
            try:
                self.client.cancel_order(o.broker_order_id)
            except DhanError as e:
                o.reason = f"cancel failed: {e}"
            return self.refresh(o, now)
        return o

    def net_position(self, security_id: str) -> Optional[int]:
        """Broker net quantity for reconciliation (UNVERIFIED field names: securityId, netQty)."""
        try:
            ps = self.client.positions()
        except DhanError:
            return None
        if not isinstance(ps, list):
            return None
        return sum(int(p.get("netQty") or 0) for p in ps if str(p.get("securityId")) == str(security_id))
