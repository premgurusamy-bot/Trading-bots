"""Data quality and freshness gate for mandatory decision inputs. Stale/missing/inconsistent data
blocks new entries; nothing is back-filled or reused as live."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Sequence

from app.core.types import Candle, DataQuality, OptionChainSnapshot, Tick
from app.strategy.params import StrategyParams

MAX_CHAIN_SPOT_DIVERGENCE = 0.005    # chain underlying vs candle close must agree within 0.5%


def check_quality(now: datetime, c5: Sequence[Candle], chain: Optional[OptionChainSnapshot],
                  tick: Optional[Tick], p: StrategyParams, require_tick: bool = True,
                  feed_faults: Optional[list[str]] = None) -> DataQuality:
    r: list[str] = list(feed_faults or [])
    done = [c for c in c5 if c.complete]
    if not done:
        r.append("No completed 5m candles")
    else:
        last = done[-1]
        close_ts = last.ts + timedelta(minutes=5)
        lag = (now - close_ts).total_seconds()
        if lag > p.max_candle_lag_s:
            r.append(f"Last completed 5m candle closed {lag:.0f}s ago (> {p.max_candle_lag_s:.0f}s)")
        if lag < -1:
            r.append("Candle timestamp is in the future (clock drift?)")
    if chain is None:
        r.append("Option chain missing")
    else:
        age = (now - chain.received_at).total_seconds()
        if age > p.max_chain_age_s:
            r.append(f"Option chain is {age:.0f}s old (> {p.max_chain_age_s:.0f}s)")
        if not chain.rows:
            r.append("Option chain has no rows")
        if chain.underlying_ltp is None or chain.underlying_ltp <= 0:
            r.append("Option chain underlying price invalid")
        elif done:
            div = abs(chain.underlying_ltp - done[-1].close) / done[-1].close
            if div > MAX_CHAIN_SPOT_DIVERGENCE:
                r.append(f"Chain underlying {chain.underlying_ltp} inconsistent with spot {done[-1].close}")
        if chain.expiry < now.date():
            r.append("Option chain expiry is in the past")
    if require_tick:
        if tick is None:
            r.append("No live tick")
        else:
            age = (now - tick.received_at).total_seconds()
            if age > p.max_tick_age_s:
                r.append(f"Last tick {age:.1f}s old (> {p.max_tick_age_s:.0f}s)")
    return DataQuality(ok=not r, reasons=r)
