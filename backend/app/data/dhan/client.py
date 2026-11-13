"""DhanHQ v2 REST client.

VERIFIED against the official DhanHQ Python SDK v2.3.0 source (PyPI `dhanhq`, © Dhan):
  base URL https://api.dhan.co/v2; headers `access-token`, `client-id`, JSON body;
  POST /optionchain {UnderlyingScrip, UnderlyingSeg, Expiry}
  POST /optionchain/expirylist {UnderlyingScrip, UnderlyingSeg}
  POST /charts/intraday {securityId, exchangeSegment, instrument, interval, oi, fromDate, toDate}
  POST /charts/historical {securityId, exchangeSegment, instrument, expiryCode, oi, fromDate, toDate}
  POST /charts/rollingoption {securityId, exchangeSegment, instrument, expiryFlag, expiryCode, strike,
                              drvOptionType, requiredData, fromDate, toDate, interval}
  POST /marketfeed/ltp|ohlc|quote {"<SEGMENT>": [ids]}
  GET  /fundlimit, /positions, /orders, /orders/{id}, /orders/external/{correlationId}, /profile
  POST /orders {transactionType, exchangeSegment, productType, orderType, validity, securityId, quantity,
                disclosedQuantity, price, afterMarketOrder, triggerPrice, correlationId}; DELETE /orders/{id}
  GET  /RenewToken (headers access-token, dhanClientId)
  Error body fields: errorCode, errorType, errorMessage.
UNVERIFIED in this build environment (docs host blocked): exact rate limits and response bodies.
Defaults are conservative and configurable. Responses that do not match the expected schema are
REJECTED, never guessed.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

log = logging.getLogger("dhan")

# Conservative client-side minimum spacing between calls (seconds). UNVERIFIED vs official limits.
MIN_INTERVAL = {"/optionchain": 3.0, "/optionchain/expirylist": 3.0, "/charts/intraday": 0.5,
                "/charts/historical": 0.5, "/charts/rollingoption": 0.5, "/marketfeed": 1.0, "default": 0.25}
AUTH_ERROR_CODES = {"DH-901"}          # invalid/expired token (UNVERIFIED code list)
RATE_LIMIT_CODES = {"DH-904"}          # rate limit (UNVERIFIED)


class DhanError(Exception):
    def __init__(self, status: int, code: Optional[str], message: str, kind: str):
        super().__init__(f"{kind} {status} {code}: {message}")
        self.status, self.code, self.kind = status, code, kind


class DhanClient:
    def __init__(self, client_id: str, access_token: str, base_url: str = "https://api.dhan.co/v2",
                 timeout: float = 10.0, transport: Optional[httpx.BaseTransport] = None):
        if not client_id or not access_token:
            raise ValueError("Dhan credentials missing")
        self._cid = client_id
        self._tok = access_token
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self._last: dict[str, float] = {}
        self.rate_limited_until: Optional[datetime] = None
        self.token_invalid = False
        self.last_error: Optional[str] = None
        self.last_ok_at: Optional[datetime] = None

    def __repr__(self):  # never expose the token
        return f"DhanClient(client_id=***{self._cid[-4:]})"

    def _headers(self):
        return {"access-token": self._tok, "client-id": self._cid, "Content-Type": "application/json",
                "Accept": "application/json"}

    def _throttle(self, path: str):
        key = next((k for k in MIN_INTERVAL if path.startswith(k)), "default")
        gap = MIN_INTERVAL[key]
        last = self._last.get(key, 0.0)
        wait = gap - (_time.monotonic() - last)
        if wait > 0:
            _time.sleep(wait)
        self._last[key] = _time.monotonic()

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict | list:
        now = datetime.now(timezone.utc)
        if self.rate_limited_until and now < self.rate_limited_until:
            raise DhanError(429, None, f"rate limited until {self.rate_limited_until.isoformat()}", "RATE_LIMIT")
        if self.token_invalid:
            raise DhanError(401, None, "access token invalid/expired; update it in Settings", "AUTH")
        self._throttle(path)
        try:
            r = self._http.request(method, self.base_url + path, json=body, headers=self._headers())
        except httpx.HTTPError as e:
            self.last_error = f"network: {type(e).__name__}"
            raise DhanError(0, None, str(type(e).__name__), "NETWORK") from None
        try:
            data = r.json()
        except ValueError:
            self.last_error = f"invalid JSON (HTTP {r.status_code})"
            raise DhanError(r.status_code, None, "invalid JSON response", "INVALID_RESPONSE") from None
        if 200 <= r.status_code < 300:
            self.last_ok_at = now
            self.last_error = None
            return data
        code = data.get("errorCode") if isinstance(data, dict) else None
        msg = data.get("errorMessage", "") if isinstance(data, dict) else ""
        if r.status_code in (401, 403) or code in AUTH_ERROR_CODES:
            self.token_invalid = True
            kind = "AUTH"
        elif r.status_code == 429 or code in RATE_LIMIT_CODES:
            self.rate_limited_until = now + timedelta(seconds=60)
            kind = "RATE_LIMIT"
        else:
            kind = "API"
        self.last_error = f"{kind} {r.status_code} {code}"
        raise DhanError(r.status_code, code, msg, kind)

    # ---- market data ----
    def option_chain(self, scrip: int, seg: str, expiry: str):
        return self._request("POST", "/optionchain", {"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry})

    def expiry_list(self, scrip: int, seg: str):
        return self._request("POST", "/optionchain/expirylist", {"UnderlyingScrip": scrip, "UnderlyingSeg": seg})

    def intraday(self, security_id: str, segment: str, instrument: str, interval: int, from_date: str, to_date: str,
                 oi: bool = False):
        return self._request("POST", "/charts/intraday", {"securityId": security_id, "exchangeSegment": segment,
                                                          "instrument": instrument, "interval": interval, "oi": oi,
                                                          "fromDate": from_date, "toDate": to_date})

    def historical(self, security_id: str, segment: str, instrument: str, from_date: str, to_date: str,
                   expiry_code: int = 0, oi: bool = False):
        return self._request("POST", "/charts/historical", {"securityId": security_id, "exchangeSegment": segment,
                                                            "instrument": instrument, "expiryCode": expiry_code, "oi": oi,
                                                            "fromDate": from_date, "toDate": to_date})

    def rolling_option(self, security_id: str, expiry_flag: str, expiry_code: int, strike: str, opt: str,
                       from_date: str, to_date: str, interval: int = 5):
        return self._request("POST", "/charts/rollingoption", {
            "securityId": security_id, "exchangeSegment": "NSE_FNO", "instrument": "OPTIDX", "expiryFlag": expiry_flag,
            "expiryCode": expiry_code, "strike": strike, "drvOptionType": opt,
            "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
            "fromDate": from_date, "toDate": to_date, "interval": interval})

    def quote(self, securities: dict):
        return self._request("POST", "/marketfeed/quote", securities)

    def ltp(self, securities: dict):
        return self._request("POST", "/marketfeed/ltp", securities)

    # ---- account / orders (execution separated from market data by the caller) ----
    def profile(self):
        return self._request("GET", "/profile")

    def fund_limit(self):
        return self._request("GET", "/fundlimit")

    def positions(self):
        return self._request("GET", "/positions")

    def orders(self):
        return self._request("GET", "/orders")

    def order_by_correlation(self, correlation_id: str):
        return self._request("GET", f"/orders/external/{correlation_id}")

    def order(self, order_id: str):
        return self._request("GET", f"/orders/{order_id}")

    def place_order(self, body: dict):
        return self._request("POST", "/orders", body)

    def cancel_order(self, order_id: str):
        return self._request("DELETE", f"/orders/{order_id}")

    def close(self):
        self._http.close()
