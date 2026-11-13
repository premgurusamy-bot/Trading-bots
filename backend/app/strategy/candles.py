"""Exchange/session-aware candle construction.

NSE equity-derivatives session: 09:15–15:30 IST. Buckets are anchored at 09:15, so 5m candles are
09:15, 09:20, … and 15m candles are 09:15, 09:30, …. A candle is `complete` only once its
bucket end time has passed (or a later bucket has started). Only complete candles may confirm
a signal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, time
from typing import Iterable, Optional, Sequence

from app.core.types import Candle, IST

SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


def bucket_start(ts: datetime, minutes: int, session_open: time = SESSION_OPEN) -> datetime:
    ts = ts.astimezone(IST)
    anchor = ts.replace(hour=session_open.hour, minute=session_open.minute, second=0, microsecond=0)
    delta = (ts - anchor).total_seconds()
    if delta < 0:
        return anchor
    k = int(delta // (minutes * 60))
    return anchor + timedelta(minutes=k * minutes)


def in_session(ts: datetime, session_open: time = SESSION_OPEN, session_close: time = SESSION_CLOSE) -> bool:
    t = ts.astimezone(IST).time()
    return session_open <= t < session_close


def resample(candles: Sequence[Candle], minutes: int, now: Optional[datetime] = None,
             session_open: time = SESSION_OPEN, session_close: time = SESSION_CLOSE) -> list[Candle]:
    """Aggregate lower-timeframe candles (sorted, same day or multi-day) into `minutes` buckets.
    Out-of-session candles are dropped. The last bucket is complete only if its end <= now (or
    when `now` is None, if it contains the bucket's final sub-candle)."""
    out: list[Candle] = []
    cur: list[Candle] = []
    cur_start: Optional[datetime] = None

    def flush(final_check: bool):
        nonlocal cur
        if not cur:
            return
        end = cur_start + timedelta(minutes=minutes)
        close_dt = datetime.combine(cur_start.date(), session_close, tzinfo=IST)
        end = min(end, close_dt)
        if final_check:
            if now is not None:
                complete = now >= end and all(c.complete for c in cur)
            else:
                last = cur[-1]
                sub = _infer_step(cur)
                complete = last.complete and (last.ts + sub >= end)
        else:
            complete = all(c.complete for c in cur)
        vols = [c.volume for c in cur]
        ois = [c.oi for c in cur if c.oi is not None]
        out.append(Candle(
            ts=cur_start, open=cur[0].open, high=max(c.high for c in cur), low=min(c.low for c in cur),
            close=cur[-1].close, volume=sum(vols), complete=complete, source=cur[0].source,
            oi=ois[-1] if ois else None,
        ))
        cur = []

    for c in candles:
        if not in_session(c.ts, session_open, session_close):
            continue
        b = bucket_start(c.ts, minutes, session_open)
        if cur_start is None or b != cur_start:
            flush(False)
            cur_start = b
        cur.append(c)
    flush(True)
    return out


def _infer_step(cs: Sequence[Candle]) -> timedelta:
    if len(cs) >= 2:
        return cs[-1].ts - cs[-2].ts
    return timedelta(minutes=1)


def completed(candles: Iterable[Candle]) -> list[Candle]:
    return [c for c in candles if c.complete]


class TickCandleBuilder:
    """Builds 1-minute candles from ticks (price + optional cumulative/ incremental volume).
    Ticks with feed timestamps older than the current candle are ignored (no back-filling)."""

    def __init__(self, source: str):
        self.source = source
        self.candles: list[Candle] = []
        self._cur: Optional[dict] = None

    def on_tick(self, ltp: float, ts: datetime, volume_delta: float = 0.0) -> Optional[Candle]:
        """Returns a just-completed candle when a new minute starts."""
        if not in_session(ts):
            return None
        b = bucket_start(ts, 1)
        done = None
        if self._cur is not None and b < self._cur["ts"]:
            return None  # late/out-of-order tick
        if self._cur is None or b > self._cur["ts"]:
            if self._cur is not None:
                done = self._finalize(True)
            self._cur = {"ts": b, "o": ltp, "h": ltp, "l": ltp, "c": ltp, "v": 0.0}
        cur = self._cur
        cur["h"] = max(cur["h"], ltp)
        cur["l"] = min(cur["l"], ltp)
        cur["c"] = ltp
        cur["v"] += max(volume_delta, 0.0)
        return done

    def _finalize(self, complete: bool) -> Candle:
        c = self._cur
        candle = Candle(ts=c["ts"], open=c["o"], high=c["h"], low=c["l"], close=c["c"], volume=c["v"],
                        complete=complete, source=self.source)
        if complete:
            self.candles.append(candle)
        return candle

    def close_if_due(self, now: datetime) -> Optional[Candle]:
        if self._cur is not None and now >= self._cur["ts"] + timedelta(minutes=1):
            done = self._finalize(True)
            self._cur = None
            return done
        return None

    def partial(self) -> Optional[Candle]:
        if self._cur is None:
            return None
        c = self._cur
        return Candle(ts=c["ts"], open=c["o"], high=c["h"], low=c["l"], close=c["c"], volume=c["v"],
                      complete=False, source=self.source)
