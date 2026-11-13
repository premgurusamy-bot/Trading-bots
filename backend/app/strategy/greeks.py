"""Black–Scholes Greeks — used ONLY when the feed does not supply Greeks and all inputs exist.

Model: European Black–Scholes on the index spot, continuous risk-free rate r (configurable,
default 6.5%), dividend yield 0, time measured in calendar years to expiry at 15:30 IST.
Limitations (also shown in the UI): NSE index options are European, but the model ignores
dividends, skew dynamics and discrete settlement. Values are labelled "calculated", and IV is
implied from the mid price (or LTP if there is no two-sided quote).
"""
from __future__ import annotations

import math
from datetime import datetime, date, time
from typing import Optional

from app.core.types import IST, OptionQuote, OptionType

EXPIRY_TIME = time(15, 30)


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def year_fraction(now: datetime, expiry: date) -> float:
    exp_dt = datetime.combine(expiry, EXPIRY_TIME, tzinfo=IST)
    return max((exp_dt - now).total_seconds(), 0.0) / (365.0 * 86400)


def bs_price(S, K, T, r, sigma, ot: OptionType) -> float:
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0) if ot == OptionType.CE else max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if ot == OptionType.CE:
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def bs_greeks(S, K, T, r, sigma, ot: OptionType) -> dict:
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sq)
    d2 = d1 - sigma * sq
    gamma = _npdf(d1) / (S * sigma * sq)
    vega = S * _npdf(d1) * sq / 100.0                       # per 1 vol point
    if ot == OptionType.CE:
        delta = _ncdf(d1)
        theta = (-S * _npdf(d1) * sigma / (2 * sq) - r * K * math.exp(-r * T) * _ncdf(d2)) / 365.0
    else:
        delta = _ncdf(d1) - 1
        theta = (-S * _npdf(d1) * sigma / (2 * sq) + r * K * math.exp(-r * T) * _ncdf(-d2)) / 365.0
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def implied_vol(price, S, K, T, r, ot: OptionType) -> Optional[float]:
    if T <= 0 or price <= 0:
        return None
    intrinsic = max(S - K * math.exp(-r * T), 0) if ot == OptionType.CE else max(K * math.exp(-r * T) - S, 0)
    if price < intrinsic - 1e-9:
        return None
    lo, hi = 1e-4, 5.0
    if bs_price(S, K, T, r, hi, ot) < price:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if bs_price(S, K, T, r, mid, ot) > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-7:
            break
    return (lo + hi) / 2


def ensure_greeks(q: OptionQuote, spot: float, expiry: date, now: datetime, r: float) -> OptionQuote:
    """Fill Greeks ONLY if the feed lacks them and inputs are sufficient. Never overwrites feed values."""
    if q.delta is not None and q.gamma is not None and q.theta is not None:
        q.greeks_source = q.greeks_source or "feed"
        return q
    T = year_fraction(now, expiry)
    px = q.mid if q.mid is not None else q.ltp
    if T <= 0 or px is None or px <= 0 or spot <= 0:
        q.greeks_source = None
        q.greeks_note = "Greeks unavailable: insufficient inputs (price/time to expiry)"
        return q
    iv_from_feed = bool(q.iv and q.iv > 0)
    sigma = (q.iv / 100.0) if iv_from_feed else implied_vol(px, spot, q.strike, T, r, q.option_type)
    if sigma is None:
        q.greeks_source = None
        q.greeks_note = "Greeks unavailable: IV could not be implied from price"
        return q
    g = bs_greeks(spot, q.strike, T, r, sigma, q.option_type)
    q.delta, q.gamma, q.theta, q.vega = g["delta"], g["gamma"], g["theta"], g["vega"]
    if q.iv is None:
        q.iv = sigma * 100
    q.greeks_source = "calculated"
    q.greeks_note = (f"Black-Scholes, r={r:.3f}, q=0, T={T*365:.3f} days, "
                     f"IV {'feed' if iv_from_feed else 'implied from ' + ('mid' if q.mid else 'LTP')}, "
                     f"at {now.isoformat()}")
    return q
