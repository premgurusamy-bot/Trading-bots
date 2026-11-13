"""Market-data provider interface. The service only consumes this interface, so the live Dhan feed,
offline replay and tests are interchangeable. Every item carries source and timestamps."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

from app.core.types import Candle, OptionChainSnapshot, OptionQuote, Tick


@dataclass
class InstrumentInfo:
    security_id: str
    exchange_segment: str
    trading_symbol: str
    expiry: Optional[date]
    strike: Optional[float]
    option_type: Optional[str]
    lot_size: int
    tick_size: Optional[float]
    master_version: Optional[int] = None


@dataclass
class FeedStatus:
    connected: bool = False
    source: str = "none"
    last_tick_at: dict = field(default_factory=dict)
    last_chain_at: dict = field(default_factory=dict)
    last_candle_at: dict = field(default_factory=dict)
    token_status: str = "unknown"
    rate_limited_until: Optional[datetime] = None
    faults: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


class MarketDataProvider(ABC):
    source: str = "abstract"

    @abstractmethod
    def candles(self, index: str, tf_minutes: int) -> list[Candle]:
        """Spot candles (volume = futures volume where configured), oldest first, incl. history."""

    @abstractmethod
    def chain(self, index: str) -> tuple[Optional[OptionChainSnapshot], Optional[OptionChainSnapshot]]:
        """(latest, previous same-session snapshot)."""

    @abstractmethod
    def tick(self, index: str) -> Optional[Tick]: ...

    @abstractmethod
    def option_quote(self, security_id: str) -> Optional[tuple[OptionQuote, datetime]]: ...

    @abstractmethod
    def instrument(self, security_id: str) -> Optional[InstrumentInfo]: ...

    @abstractmethod
    def daily_prev(self, index: str, today: date) -> Optional[Candle]: ...

    @abstractmethod
    def status(self) -> FeedStatus: ...

    def futures_oi_change(self, index: str) -> tuple[Optional[float], Optional[float]]:
        return None, None

    def start(self):  # pragma: no cover - overridden by live providers
        pass

    def stop(self):  # pragma: no cover
        pass


class ReplayProvider(MarketDataProvider):
    """Deterministic provider for tests, demos and paper replay. Data is pushed in by the caller and is
    labelled with its own source (e.g. SYNTHETIC or a stored Dhan capture)."""
    source = "replay"

    def __init__(self):
        self._c1: dict[str, list[Candle]] = {}
        self._c5: dict[str, list[Candle]] = {}
        self._c15: dict[str, list[Candle]] = {}
        self._chains: dict[str, list[OptionChainSnapshot]] = {}
        self._ticks: dict[str, Tick] = {}
        self._quotes: dict[str, tuple[OptionQuote, datetime]] = {}
        self._instr: dict[str, InstrumentInfo] = {}
        self._daily: dict[str, Candle] = {}
        self._status = FeedStatus(connected=True, source="replay", token_status="n/a")

    def set_candles(self, index, tf, cs):
        {1: self._c1, 5: self._c5, 15: self._c15}[tf][index] = list(cs)

    def push_chain(self, index, ch: OptionChainSnapshot):
        self._chains.setdefault(index, []).append(ch)
        for r in ch.rows:
            if r.security_id:
                self._quotes[r.security_id] = (r, ch.received_at)

    def set_tick(self, index, t: Tick):
        self._ticks[index] = t
        self._status.last_tick_at[index] = t.received_at

    def set_quote(self, security_id, q: OptionQuote, at: datetime):
        self._quotes[security_id] = (q, at)

    def set_instrument(self, info: InstrumentInfo):
        self._instr[info.security_id] = info

    def set_daily_prev(self, index, c: Candle):
        self._daily[index] = c

    def candles(self, index, tf_minutes):
        return list({1: self._c1, 5: self._c5, 15: self._c15}[tf_minutes].get(index, []))

    def chain(self, index):
        lst = self._chains.get(index, [])
        cur = lst[-1] if lst else None
        prev = None
        if cur is not None:
            same = [c for c in lst[:-1] if c.expiry == cur.expiry and c.received_at.date() == cur.received_at.date()]
            prev = same[-1] if same else None
        return cur, prev

    def tick(self, index):
        return self._ticks.get(index)

    def option_quote(self, security_id):
        return self._quotes.get(security_id)

    def instrument(self, security_id):
        return self._instr.get(security_id)

    def daily_prev(self, index, today):
        return self._daily.get(index)

    def status(self):
        return self._status
