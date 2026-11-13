"""Normalized market-data types shared by the engine, adapters, paper broker and backtester.

Every market-data item carries its source and the exchange/feed timestamp where available,
plus the local receive time, so freshness can be checked and decisions reproduced.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class Index(str, Enum):
    NIFTY = "NIFTY"
    BANKNIFTY = "BANKNIFTY"


class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


@dataclass(frozen=True)
class Candle:
    """One OHLCV candle. `ts` is the candle OPEN time (timezone-aware, IST)."""
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    complete: bool = True
    source: str = "unknown"
    oi: Optional[float] = None

    def __post_init__(self):
        if self.ts.tzinfo is None:
            raise ValueError("Candle timestamp must be timezone-aware")
        if not (self.high >= max(self.open, self.close) and self.low <= min(self.open, self.close)):
            raise ValueError(f"Inconsistent OHLC at {self.ts}: {self.open},{self.high},{self.low},{self.close}")
        if self.volume < 0:
            raise ValueError("Negative volume")

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return d


@dataclass
class OptionQuote:
    """One option contract row from an option-chain snapshot.

    Greek fields are None when not supplied by the feed. `greeks_source` is "feed" when Dhan
    supplied them, "calculated" when computed by app.strategy.greeks, or None.
    """
    strike: float
    option_type: OptionType
    security_id: Optional[str] = None
    ltp: Optional[float] = None
    oi: Optional[float] = None
    prev_oi: Optional[float] = None
    volume: Optional[float] = None
    prev_close: Optional[float] = None
    bid: Optional[float] = None
    bid_qty: Optional[float] = None
    ask: Optional[float] = None
    ask_qty: Optional[float] = None
    iv: Optional[float] = None          # percent, e.g. 14.2
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None       # premium change per calendar day
    vega: Optional[float] = None
    greeks_source: Optional[str] = None
    greeks_note: Optional[str] = None

    @property
    def oi_change(self) -> Optional[float]:
        if self.oi is None or self.prev_oi is None:
            return None
        return self.oi - self.prev_oi

    @property
    def mid(self) -> Optional[float]:
        if self.bid is None or self.ask is None or self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            return None
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Optional[float]:
        if self.mid is None:
            return None
        return self.ask - self.bid

    def to_dict(self) -> dict:
        d = asdict(self)
        d["option_type"] = self.option_type.value
        return d


@dataclass
class OptionChainSnapshot:
    index: Index
    expiry: date
    underlying_ltp: float
    rows: list[OptionQuote]
    source: str
    feed_ts: Optional[datetime]       # exchange/feed time if supplied
    received_at: datetime             # local receive time (IST)
    raw_ref: Optional[str] = None     # reference to stored raw payload

    def strikes(self) -> list[float]:
        return sorted({r.strike for r in self.rows})

    def get(self, strike: float, ot: OptionType) -> Optional[OptionQuote]:
        for r in self.rows:
            if r.strike == strike and r.option_type == ot:
                return r
        return None

    def to_dict(self) -> dict:
        return {
            "index": self.index.value,
            "expiry": self.expiry.isoformat(),
            "underlying_ltp": self.underlying_ltp,
            "source": self.source,
            "feed_ts": self.feed_ts.isoformat() if self.feed_ts else None,
            "received_at": self.received_at.isoformat(),
            "raw_ref": self.raw_ref,
            "rows": [r.to_dict() for r in self.rows],
        }


@dataclass
class Tick:
    index_or_symbol: str
    ltp: float
    feed_ts: Optional[datetime]
    received_at: datetime
    source: str


@dataclass
class DataQuality:
    ok: bool
    reasons: list[str] = field(default_factory=list)
