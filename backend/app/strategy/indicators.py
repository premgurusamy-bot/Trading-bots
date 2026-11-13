"""Deterministic technical indicators. Pure functions over lists; output index i uses data[0..i] only
(no look-ahead). Undefined warm-up values are None.

Conventions (documented in docs/STRATEGY_V1.md):
* EMA: alpha = 2/(n+1), seeded with the SMA of the first n values (TradingView `ta.ema` compatible
  after warm-up convergence).
* ATR / RSI / ADX: Wilder smoothing (RMA, alpha = 1/n) seeded with the SMA of the first n values.
* VWAP: session-anchored; resets at the first candle of each trading date (IST);
  typical price (H+L+C)/3 weighted by volume.
"""
from __future__ import annotations

from typing import Optional, Sequence

from app.core.types import Candle

Series = list[Optional[float]]


def sma(values: Sequence[Optional[float]], n: int) -> Series:
    out: Series = [None] * len(values)
    window: list[float] = []
    s = 0.0
    for i, v in enumerate(values):
        if v is None:
            window.clear()
            s = 0.0
            continue
        window.append(v)
        s += v
        if len(window) > n:
            s -= window.pop(0)
        if len(window) == n:
            out[i] = s / n
    return out


def ema(values: Sequence[float], n: int) -> Series:
    out: Series = [None] * len(values)
    if len(values) < n:
        return out
    alpha = 2.0 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def rma(values: Sequence[float], n: int) -> Series:
    """Wilder's moving average."""
    out: Series = [None] * len(values)
    if len(values) < n:
        return out
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = (prev * (n - 1) + values[i]) / n
        out[i] = prev
    return out


def true_range(candles: Sequence[Candle]) -> list[float]:
    tr = []
    for i, c in enumerate(candles):
        if i == 0:
            tr.append(c.high - c.low)
        else:
            pc = candles[i - 1].close
            tr.append(max(c.high - c.low, abs(c.high - pc), abs(c.low - pc)))
    return tr


def atr(candles: Sequence[Candle], n: int = 14) -> Series:
    return rma(true_range(candles), n)


def rsi(closes: Sequence[float], n: int = 14) -> Series:
    out: Series = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [0.0]
    losses = [0.0]
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[1:n + 1]) / n
    al = sum(losses[1:n + 1]) / n

    def _val(g, l):
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1 + g / l)

    out[n] = _val(ag, al)
    for i in range(n + 1, len(closes)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
        out[i] = _val(ag, al)
    return out


def adx(candles: Sequence[Candle], n: int = 14) -> tuple[Series, Series, Series]:
    """Returns (adx, +DI, -DI) using Wilder's method."""
    m = len(candles)
    plus_dm = [0.0] * m
    minus_dm = [0.0] * m
    for i in range(1, m):
        up = candles[i].high - candles[i - 1].high
        dn = candles[i - 1].low - candles[i].low
        plus_dm[i] = up if (up > dn and up > 0) else 0.0
        minus_dm[i] = dn if (dn > up and dn > 0) else 0.0
    tr = true_range(candles)
    # Wilder smoothing starting from index 1 (first diff)
    pdi: Series = [None] * m
    mdi: Series = [None] * m
    dx: list[Optional[float]] = [None] * m
    if m <= n:
        return [None] * m, pdi, mdi
    s_tr = sum(tr[1:n + 1])
    s_p = sum(plus_dm[1:n + 1])
    s_m = sum(minus_dm[1:n + 1])
    for i in range(n, m):
        if i > n:
            s_tr = s_tr - s_tr / n + tr[i]
            s_p = s_p - s_p / n + plus_dm[i]
            s_m = s_m - s_m / n + minus_dm[i]
        if s_tr == 0:
            pdi[i] = mdi[i] = 0.0
            dx[i] = 0.0
            continue
        p = 100 * s_p / s_tr
        q = 100 * s_m / s_tr
        pdi[i], mdi[i] = p, q
        dx[i] = 0.0 if (p + q) == 0 else 100 * abs(p - q) / (p + q)
    adx_out: Series = [None] * m
    first = 2 * n - 1
    if m > first:
        prev = sum(dx[n:first + 1]) / n  # type: ignore[arg-type]
        adx_out[first] = prev
        for i in range(first + 1, m):
            prev = (prev * (n - 1) + dx[i]) / n  # type: ignore[operator]
            adx_out[i] = prev
    return adx_out, pdi, mdi


def session_vwap(candles: Sequence[Candle], volumes: Optional[Sequence[Optional[float]]] = None) -> Series:
    """Session VWAP reset each IST trading date. `volumes` overrides candle.volume (used when price
    comes from the index spot and volume from index futures). A candle with missing/zero volume
    keeps the previous VWAP; if no volume has accrued in the session the value is None
    (never fabricated)."""
    out: Series = [None] * len(candles)
    cur_date = None
    pv = 0.0
    vv = 0.0
    for i, c in enumerate(candles):
        d = c.ts.date()
        if d != cur_date:
            cur_date = d
            pv = vv = 0.0
        v = volumes[i] if volumes is not None else c.volume
        if v:
            tp = (c.high + c.low + c.close) / 3
            pv += tp * v
            vv += v
        out[i] = pv / vv if vv > 0 else None
    return out


def slope(series: Series, i: int, lookback: int) -> Optional[float]:
    """Average per-bar change of series over `lookback` bars ending at i."""
    j = i - lookback
    if j < 0 or series[i] is None or series[j] is None:
        return None
    return (series[i] - series[j]) / lookback  # type: ignore[operator]


def crosses(a: Series, b: Sequence[Optional[float]], start: int, end: int) -> int:
    """Number of sign changes of (a-b) in [start, end]."""
    n = 0
    prev_sign = 0
    for i in range(max(start, 0), end + 1):
        if a[i] is None or b[i] is None:
            continue
        d = a[i] - b[i]  # type: ignore[operator]
        s = 1 if d > 0 else (-1 if d < 0 else 0)
        if s != 0:
            if prev_sign != 0 and s != prev_sign:
                n += 1
            prev_sign = s
    return n
