"""Bar-replay backtest simulator.

Kinds of test (always labelled in the results):
* "technical_only": genuine underlying candles only. Derivatives/OI/Greeks/premium checks and the 75-point
  gate are NOT evaluated. Outcomes are in underlying R-multiples. This is NOT the full strategy.
* "technical_with_premium_overlay": as above, plus genuine historical ATM option premiums (Dhan rolling-option
  data) used for rupee P&L of 1 lot with costs. Greeks/OI filters are still not applied.
* "full": only when genuine timestamped historical option-chain snapshots are supplied (not available from
  the documented Dhan endpoints; reserved for user-captured snapshot replays).

No look-ahead: at bar i the engine sees candles[0..i] only; 15m candles are rebuilt from that prefix and
the forming 15m candle is excluded. Fills are conservative: the trigger fills at max(trigger, bar open) +
slippage; if a bar reaches both the stop and a target, the STOP is assumed first.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, date
from typing import Optional, Sequence

from app.core.types import Candle, DataQuality
from app.risk.charges import ChargeSchedule, leg_charges
from app.strategy import indicators as ind
from app.strategy.candles import resample
from app.strategy.engine import MarketContext, evaluate, HISTORY_BARS_5M
from app.strategy.levels import previous_day_from_intraday
from app.strategy.params import StrategyParams


@dataclass
class BTTrade:
    index: str
    setup: str
    direction: str
    signal_ts: datetime
    opened_at: datetime
    entry: float
    stop: float
    t1: float
    t2: float
    t3: Optional[float]
    risk: float
    exit_ts: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    r_multiple: Optional[float] = None
    t1_hit: bool = False
    prem_entry: Optional[float] = None
    prem_exit: Optional[float] = None
    gross: Optional[float] = None
    charges: Optional[float] = None
    net: Optional[float] = None
    score_technical: Optional[int] = None

    def to_dict(self):
        d = asdict(self)
        for k in ("signal_ts", "opened_at", "exit_ts"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


def run_backtest(index: str, c5_all: Sequence[Candle], p: StrategyParams, premiums: Optional[dict] = None,
                 lot_size: Optional[int] = None, schedule: Optional[ChargeSchedule] = None) -> dict:
    """premiums: {"CE": {ts: close}, "PE": {ts: close}} genuine ATM premiums aligned to 5m candle open times."""
    c5_all = [c for c in c5_all if c.complete]
    trades: list[BTTrade] = []
    pending = None
    open_t: Optional[BTTrade] = None
    day_entries: dict[date, int] = {}
    candidates = 0
    rejected_reasons: dict[str, int] = {}
    warm = max(p.ema_slow, 2 * p.adx_len) + 2
    for i in range(len(c5_all)):
        bar = c5_all[i]
        d = bar.ts.date()
        # 1) manage open trade on this bar (conservative ordering)
        if open_t is not None:
            _manage(open_t, bar, c5_all[: i + 1], p, premiums)
            if open_t.exit_ts is not None:
                trades.append(open_t)
                open_t = None
        # 2) pending signal trigger on this bar
        if pending is not None and open_t is None:
            sig, expires = pending
            if bar.ts >= expires or bar.ts.date() != sig["ts"].date():
                pending = None
            else:
                trig = sig["plan"]["trigger"]
                hit = bar.high >= trig if sig["direction"] == "BULLISH" else bar.low <= trig
                if hit and day_entries.get(d, 0) < p.max_entries_per_index_per_day and bar.ts.time() <= p.entry_cutoff:
                    slip = p.slippage_ticks * p.tick_size
                    px = (max(trig, bar.open) + slip) if sig["direction"] == "BULLISH" else (min(trig, bar.open) - slip)
                    pl = sig["plan"]
                    open_t = BTTrade(index, sig["setup"], sig["direction"], sig["ts"], bar.ts, px, pl["stop"], pl["t1"],
                                     pl["t2"], pl["t3"], abs(px - pl["stop"]), score_technical=sig["score"])
                    if premiums:
                        side = "CE" if sig["direction"] == "BULLISH" else "PE"
                        pe = premiums.get(side, {}).get(bar.ts)
                        open_t.prem_entry = (pe + slip) if pe else None
                    day_entries[d] = day_entries.get(d, 0) + 1
                    pending = None
                    _manage(open_t, bar, c5_all[: i + 1], p, premiums, entry_bar=True)
                    if open_t.exit_ts is not None:
                        trades.append(open_t)
                        open_t = None
        # 3) evaluate the completed bar (no look-ahead)
        if i < warm or open_t is not None or pending is not None:
            continue
        lo = max(0, i - HISTORY_BARS_5M - 200)
        prefix = c5_all[lo: i + 1]
        close_time = bar.ts + timedelta(minutes=5)
        c15 = [c for c in resample(prefix, 15, now=close_time) if c.complete]
        ctx = MarketContext(index, list(prefix), c15, None, None, previous_day_from_intraday(prefix, d), close_time,
                            DataQuality(True, []), {"ok": day_entries.get(d, 0) < p.max_entries_per_index_per_day, "reasons": []})
        res = evaluate(ctx, p, technical_only=True)
        if res.candidate:
            failed = res.candidate["failed"]
            if not failed:
                candidates += 1
                tech = sum(c["points"] for c in res.candidate["score"]["components"]
                           if c["key"] in ("ema_alignment", "vwap_position", "structure_15m", "sr_confluence",
                                           "rejection_candle", "volume", "fib_trendline", "candle_formation"))
                pending = ({"ts": bar.ts, "direction": res.candidate["direction"], "setup": res.candidate["setup"],
                            "plan": res.candidate["plan"], "score": tech},
                           bar.ts + timedelta(minutes=5 * (1 + p.signal_validity_candles)))
            else:
                for f in failed:
                    rejected_reasons[f] = rejected_reasons.get(f, 0) + 1
    if open_t is not None:
        open_t.exit_ts, open_t.exit_price, open_t.exit_reason = c5_all[-1].ts, c5_all[-1].close, "data end"
        _finalise(open_t, premiums, c5_all[-1].ts)
        trades.append(open_t)
    schedule = schedule or ChargeSchedule()
    for t in trades:
        if t.prem_entry is not None and t.prem_exit is not None and lot_size:
            q = lot_size
            t.gross = round((t.prem_exit - t.prem_entry) * q, 2)
            ch = leg_charges("BUY", t.prem_entry * q, 1, t.opened_at.date(), schedule).total + \
                leg_charges("SELL", t.prem_exit * q, 1, t.exit_ts.date(), schedule).total
            t.charges = round(ch, 2)
            t.net = round(t.gross - ch, 2)
    return {"trades": trades, "technical_candidates": candidates, "rejected_reasons": rejected_reasons}


def _manage(t: BTTrade, bar: Candle, hist: Sequence[Candle], p: StrategyParams, premiums, entry_bar: bool = False):
    bull = t.direction == "BULLISH"
    if bar.ts.time() >= p.squareoff_time:
        t.exit_ts, t.exit_price, t.exit_reason = bar.ts, bar.open, "EOD square-off"
        return _finalise(t, premiums, bar.ts)
    stop_hit = bar.low <= t.stop if bull else bar.high >= t.stop
    if stop_hit:                                         # stop assumed before any target in the same bar
        px = min(t.stop, bar.open) if bull else max(t.stop, bar.open)
        if entry_bar:
            px = t.stop
        t.exit_ts, t.exit_price, t.exit_reason = bar.ts, px, ("trail/breakeven stop" if t.t1_hit else "stop")
        return _finalise(t, premiums, bar.ts)
    fav = bar.high if bull else bar.low
    if not t.t1_hit and ((fav >= t.t1) if bull else (fav <= t.t1)):
        t.t1_hit = True
        t.stop = t.entry                                    # breakeven (costs handled in rupee overlay)
    final = t.t3 if t.t3 is not None else t.t2
    if t.t1_hit and ((fav >= final) if bull else (fav <= final)):
        t.exit_ts, t.exit_price, t.exit_reason = bar.ts, final, "final target"
        return _finalise(t, premiums, bar.ts)
    if t.t1_hit and bar.complete:
        closes = [c.close for c in hist]
        e9 = ind.ema(closes, p.ema_fast)[-1]
        a = ind.atr(list(hist), p.atr_len)[-1]
        if e9 is not None and a is not None:
            tr = e9 - p.trail_atr_mult * a if bull else e9 + p.trail_atr_mult * a
            t.stop = max(t.stop, tr) if bull else min(t.stop, tr)


def _finalise(t: BTTrade, premiums, ts):
    sgn = 1 if t.direction == "BULLISH" else -1
    # R multiple: T1 half booked at +1R when hit (blended), remainder at exit
    rem = sgn * (t.exit_price - t.entry) / t.risk if t.risk > 0 else 0.0
    t.r_multiple = round(0.5 * 1.0 + 0.5 * rem, 3) if t.t1_hit else round(rem, 3)
    if premiums:
        side = "CE" if t.direction == "BULLISH" else "PE"
        px = premiums.get(side, {}).get(ts)
        t.prem_exit = max(px - 2 * 0.05, 0.05) if px else None
