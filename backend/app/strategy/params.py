"""Frozen Strategy V1.0 parameters.

Two kinds of values:

* SPEC values come straight from the frozen V1.0 specification. Do not change the defaults.
* IMPL values were not given a number in the spec. The specification asked for them to be
  configurable with conservative, documented defaults. They are listed in
  docs/STRATEGY_V1.md ("Implementer defaults pending approval") and can be changed only in
  Settings, with confirmation and an audit record.

`StrategyParams.defaults_changed()` reports which values differ from these defaults, so the UI
can show whether the frozen defaults have been modified.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, fields, replace
from datetime import time

STRATEGY_VERSION = "V1.0"


@dataclass(frozen=True)
class StrategyParams:
    # ---------------- SPEC: indicators ----------------
    ema_fast: int = 9
    ema_slow: int = 20
    atr_len: int = 14
    volume_sma_len: int = 20
    rsi_len: int = 14                       # optional confirmation only; not scored
    adx_len: int = 14
    pivot_len: int = 5                      # confirmed pivot: 5 bars each side
    fib_levels: tuple = (0.382, 0.5, 0.618, 0.786)
    min_score: int = 75
    strong_score: int = 85
    signal_validity_candles: int = 3        # completed 5m candles
    max_entries_per_index_per_day: int = 2
    max_active_positions_per_index: int = 1
    # Greeks: preferred |delta| band
    delta_min: float = 0.45
    delta_max: float = 0.65
    # Targets (R multiples)
    t1_r: float = 1.0
    t2_r: float = 2.0
    t3_r: float = 3.0
    t1_book_fraction: float = 0.5

    # ---------------- IMPL: trend / sideways filter ----------------
    ema_slope_lookback: int = 3             # bars used to measure EMA9 slope
    flat_slope_atr_frac: float = 0.05       # |slope| per bar < this * ATR => flat
    adx_min_trend: float = 20.0             # ADX below this => insufficient directional strength
    sideways_lookback: int = 12             # 5m bars examined for chop
    max_ema_crosses: int = 2                # more crosses than this in lookback => sideways
    max_vwap_crosses: int = 3               # more VWAP crossings than this => sideways
    # ---------------- IMPL: S/R engine ----------------
    zone_atr_frac: float = 0.25             # levels within this * ATR are "overlapping"
    touch_atr_frac: float = 0.30            # price "reaches" a level if within this * ATR
    opening_range_minutes: int = 15
    # ---------------- IMPL: entry / exit ----------------
    sl_atr_buffer: float = 0.10             # SL = rejection low - 0.10*ATR (configurable buffer)
    max_entry_candle_atr: float = 1.5       # don't chase: confirmation candle range > 1.5*ATR => reject
    max_entry_ext_atr: float = 1.5          # don't chase: trigger farther than 1.5*ATR from EMA9 => reject
    min_rr_to_next_sr: float = 2.0          # next major S/R must allow at least T2 (1:2)
    volume_confirm_mult: float = 1.0        # confirmation candle volume >= 1.0 x SMA20
    breakout_volume_mult: float = 1.5       # Setup C breakout candle volume >= 1.5 x SMA20
    trail_atr_mult: float = 1.0             # trail = EMA9 -/+ 1.0*ATR after T1
    single_lot_t1_action: str = "exit_all"  # with 1 lot, 50% booking is impossible
    retest_max_candles: int = 6             # Setup C: retest must occur within N 5m candles of breakout
    # ---------------- IMPL: option selection ----------------
    min_option_volume: float = 1000.0       # contracts traded today (feed units)
    min_option_oi: float = 1000.0
    max_spread_pct: float = 1.5             # (ask-bid)/mid*100
    max_spread_abs: float = 2.0             # rupees; spread acceptable if <= either bound
    max_theta_pct_of_premium: float = 15.0  # |theta|/premium*100 per day
    iv_spike_ratio: float = 1.30            # IV > 1.30 x near-ATM median IV => abnormal spike
    min_gamma: float = 0.0                  # must be strictly positive
    atm_strike_window: int = 10             # strikes each side of ATM shown/analysed
    near_atm_pcr_strikes: int = 3           # ATM +/- 3 strikes for near-ATM PCR
    # ---------------- IMPL: session ----------------
    session_open: time = time(9, 15)
    session_close: time = time(15, 30)
    entry_cutoff: time = time(14, 45)       # no new entries after
    squareoff_time: time = time(15, 15)     # forced EOD exit
    # ---------------- IMPL: data freshness (seconds) ----------------
    max_tick_age_s: float = 5.0
    max_chain_age_s: float = 30.0
    max_candle_lag_s: float = 90.0          # last completed 5m candle must close within this of now
    # ---------------- IMPL: Greeks model ----------------
    risk_free_rate: float = 0.065           # used only when Greeks must be calculated
    # ---------------- IMPL: execution assumptions ----------------
    slippage_ticks: int = 2
    tick_size: float = 0.05
    volume_source: str = "futures"          # index spot has no volume; use current-month futures

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, time):
                d[k] = v.strftime("%H:%M")
            elif isinstance(v, tuple):
                d[k] = list(v)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyParams":
        base = cls()
        kwargs = {}
        valid = {f.name: f for f in fields(cls)}
        for k, v in d.items():
            if k not in valid:
                continue
            cur = getattr(base, k)
            if isinstance(cur, time) and isinstance(v, str):
                hh, mm = v.split(":")
                v = time(int(hh), int(mm))
            elif isinstance(cur, tuple):
                v = tuple(v)
            elif isinstance(cur, bool):
                v = bool(v)
            elif isinstance(cur, int):
                v = int(v)
            elif isinstance(cur, float):
                v = float(v)
            kwargs[k] = v
        return replace(base, **kwargs)

    def defaults_changed(self) -> dict:
        base = StrategyParams().to_dict()
        mine = self.to_dict()
        return {k: {"default": base[k], "current": mine[k]} for k in base if base[k] != mine[k]}


SPEC_FIELDS = {
    "ema_fast", "ema_slow", "atr_len", "volume_sma_len", "rsi_len", "adx_len", "pivot_len",
    "fib_levels", "min_score", "strong_score", "signal_validity_candles",
    "max_entries_per_index_per_day", "max_active_positions_per_index", "delta_min", "delta_max",
    "t1_r", "t2_r", "t3_r", "t1_book_fraction",
}
