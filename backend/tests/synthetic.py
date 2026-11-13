"""SYNTHETIC test fixtures — NOT market data. Used only by unit tests to exercise deterministic logic.
Nothing here may be presented as real-market validation."""
from __future__ import annotations

from datetime import datetime, date, timedelta, time

from app.core.types import Candle, IST, OptionChainSnapshot, OptionQuote, OptionType, Index, Tick, DataQuality
from app.strategy import indicators as ind

SRC = "SYNTHETIC"


def c(ts, o, h, l, cl, v=1000.0, complete=True) -> Candle:
    return Candle(ts=ts, open=o, high=h, low=l, close=cl, volume=v, complete=complete, source=SRC)


def ts(d: date, hh: int, mm: int) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=IST)


def session_times(d: date, minutes: int = 5) -> list[datetime]:
    out, t = [], ts(d, 9, 15)
    end = ts(d, 15, 30)
    while t < end:
        out.append(t)
        t += timedelta(minutes=minutes)
    return out


def path_candles(d: date, closes: list[float], wick: float = 4.0, vol: float = 1000.0, start_open=None) -> list[Candle]:
    tms = session_times(d)
    out = []
    prev = start_open if start_open is not None else closes[0]
    for k, cl in enumerate(closes):
        o = prev
        h = max(o, cl) + wick
        l = min(o, cl) - wick
        out.append(c(tms[k], o, h, l, cl, vol))
        prev = cl
    return out


def uptrend_closes(n: int, start: float, up=6.0, down=-3.0, up_bars=8, down_bars=6) -> list[float]:
    out, p, k = [], start, 0
    while len(out) < n:
        for _ in range(up_bars):
            p += up
            out.append(p)
        for _ in range(down_bars):
            p += down
            out.append(p)
    return out[:n]


def bullish_setup_a(shift_to_strike: bool = True, day2_bars: int = 24):
    """Day 1 + Day 2 uptrend, ending with a hammer whose wick probes EMA20 at a put-OI strike.
    Returns (c5, c15, daily_prev, conf_candle, ema20_at_conf, strike_step)."""
    d1, d2 = date(2026, 11, 9), date(2026, 11, 10)
    day1 = path_candles(d1, uptrend_closes(75, 24000.0), wick=5)
    day2_closes = uptrend_closes(day2_bars, day1[-1].close + 2, up=4.0, down=-2.0)
    day2 = path_candles(d2, day2_closes, wick=5, start_open=day1[-1].close)
    hist = day1 + day2
    closes = [x.close for x in hist]
    e20 = ind.ema(closes, 20)[-1]
    e9 = ind.ema(closes, 9)[-1]
    last = hist[-1]
    # Hammer: opens at last close, probes down to EMA20, closes near the high
    t_conf = last.ts + timedelta(minutes=5)
    o = last.close
    cl = o + 2
    hi = cl + 0.5
    lo = e20 - 1.0
    hammer = c(t_conf, o, hi, lo, cl, v=2500.0)
    allc = hist + [hammer]
    offset = 0.0
    if shift_to_strike:
        e20n = ind.ema([x.close for x in allc], 20)[-1]
        offset = round(e20n / 50) * 50 - e20n
        allc = [c(x.ts, x.open + offset, x.high + offset, x.low + offset, x.close + offset, x.volume) for x in allc]
    c5 = allc
    from app.strategy.candles import resample
    c15 = resample(c5, 15)
    from app.strategy.levels import previous_day_from_intraday
    dp = previous_day_from_intraday(c5, d2)
    return c5, c15, dp, c5[-1]


def chain_for(spot: float, d: date, put_wall: float, call_wall: float, bullish=True, step=50.0, n=12,
              received_at=None, expiry=None, mp_strike=None) -> OptionChainSnapshot:
    atm = round(spot / step) * step
    rows = []
    for k in range(-n, n + 1):
        K = atm + k * step
        mny = (spot - K) / step  # >0 => CE ITM
        ce_delta = max(0.03, min(0.97, 0.5 + 0.09 * mny))
        pe_delta = ce_delta - 1
        ce_ltp = max(1.0, 120 + 40 * mny) if mny > -3 else max(1.0, 30 + 5 * (mny + 3))
        pe_ltp = max(1.0, 120 - 40 * mny) if mny < 3 else max(1.0, 30 - 5 * (mny - 3))
        base_oi = 100000.0
        ce_oi = base_oi * (5 if K == call_wall else 1)
        pe_oi = base_oi * (5 if K == put_wall else 1) + (400000.0 if K == mp_strike else 0.0)
        # bullish day-change behaviour: puts written (OI up, premium down), calls unwound (OI down, premium up)
        if bullish:
            ce_prev_oi, pe_prev_oi = ce_oi * 1.10, pe_oi * 0.90
            ce_prev_close, pe_prev_close = ce_ltp * 0.9, pe_ltp * 1.1
        else:
            ce_prev_oi, pe_prev_oi = ce_oi * 0.90, pe_oi * 1.10
            ce_prev_close, pe_prev_close = ce_ltp * 1.1, pe_ltp * 0.9
        g = 0.0012 * max(0.1, 1 - abs(mny) * 0.15)
        for ot, ltp, dl, oi, poi, pc in ((OptionType.CE, ce_ltp, ce_delta, ce_oi, ce_prev_oi, ce_prev_close),
                                         (OptionType.PE, pe_ltp, pe_delta, pe_oi, pe_prev_oi, pe_prev_close)):
            rows.append(OptionQuote(strike=K, option_type=ot, security_id=f"S{int(K)}{ot.value}", ltp=ltp, oi=oi,
                                    prev_oi=poi, volume=50000.0, prev_close=pc, bid=ltp - 0.1, ask=ltp + 0.1,
                                    bid_qty=650, ask_qty=650, iv=13.0, delta=dl, gamma=g, theta=-6.0, vega=10.0,
                                    greeks_source="feed"))
    now = received_at or datetime.combine(d, time(12, 0), tzinfo=IST)
    return OptionChainSnapshot(Index.NIFTY, expiry or (d + timedelta(days=7)), spot, rows, SRC, now, now)


def ok_quality() -> DataQuality:
    return DataQuality(True, [])
