# Strategy V1.0 — as implemented

Source of truth: `backend/app/strategy/` (params in `params.py`). This document states every rule that
the code applies. **SPEC** = value or rule from the frozen V1.0 specification. **IMPL** = the spec required
a rule but gave no number; the conservative default chosen is listed below and needs your approval.

## Data model
* Main timeframe 5m, trend timeframe 15m, and 1m for display. Candles are session-anchored at 09:15 IST.
  Only **completed** candles are used. A 15m candle counts only once its full period has ended
  before the close of the 5m candle being evaluated (no look-ahead).
* **Volume (IMPL, data decision):** index spot has no traded volume on NSE/Dhan. Volume and VWAP use
  the **current-month index futures** volume at the same candle timestamp. If futures volume is
  missing, volume stays 0: the volume component scores 0, the volume mandatory check fails, and
  VWAP stays None. Nothing is fabricated.
* VWAP = Σ(typical price × futures volume) / Σ futures volume, reset each IST trading date.
* Indicators: EMA (SMA-seeded), Wilder ATR/RSI/ADX, SMA. RSI 14 is computed and displayed as
  information only. It is never scored and never gates.
* Fixed warm-up window: the last 300 completed 5m bars and 120 15m bars, identical in live and backtest.

## Trend filters (all must hold; SPEC)
Bullish: 15m EMA9 > EMA20 · 5m EMA9 > EMA20 · close > VWAP · EMA9 slope positive · no confirmed bearish
structure break. Bearish is the exact mirror.
* Slope = (EMA9[i] − EMA9[i−3]) / 3 (IMPL lookback 3). "Positive" means > 0.05 × ATR per bar (IMPL).
* Confirmed bearish structure break: a completed close below the most recent confirmed swing low,
  at any point since that low was confirmed.

## Sideways / no-trade filter (IMPL thresholds, SPEC conditions)
Sideways if ANY of these is true over the last 12 5m bars:
EMA9/20 crosses > 2 · VWAP crossings > 3 · flat EMA9 slope (|slope| ≤ 0.05 ATR) · ADX(14) < **20** ·
no HH/HL or LH/LL structure on BOTH 5m and 15m.

## Confirmed pivots (SPEC length 5)
A pivot high at bar p: high[p] > each of the 5 previous highs and ≥ each of the 5 following highs. It is
known only at bar p+5 and never moves after that. Lows mirror this.

## Support/resistance engine
| Family | Levels | Kind |
|---|---|---|
| prev_day | PDH, PDL, PDC (from the previous session's genuine intraday data) | R, S, both |
| opening_range | ORH/ORL of the first 15 minutes (IMPL 15); valid once complete | R, S |
| swing | last 4 confirmed 5m and 15m swing highs/lows | R / S |
| zone | demand/supply: the base candle before an impulse candle (body ≥ 1.5 ATR, closing beyond the base); removed when a later close breaks through it | S / R |
| fib | 38.2/50/61.8/78.6% of the latest confirmed swing leg | S on up-leg, R on down-leg |
| vwap, ema20 | dynamic | both |
| oi | top-2 PE OI strikes (support) and CE OI strikes (resistance) | S / R |
| trendline | through the last two confirmed swing lows (rising) or highs (falling), unbroken by any completed close since | S / R |

Overlap tolerance is 0.25 × ATR (IMPL). Confluence counts **independent families**: two families = important,
three or more = high. Each level carries source, family, price/zone, timestamp, confirmation and strength.

## Setups
**A (bullish pullback/rejection) / B (mirror).** Mandatory checks, all of which must pass:
1. trend filters above (15m trend, 5m EMA alignment, VWAP side, slope, no opposing break), not sideways
2. reaction at a level: the confirmation candle or the one before it trades within 0.30 × ATR (IMPL) of
   a support level (or pierces it), and the confirmation candle closes back above it
3. derivatives confirmation: at least one present supporting evidence item, and strictly more
   supporting than opposing items (see Option chain)
4. selected CE/PE passes every contract check (see Greeks and option selection)
5. a recognised rejection pattern on the completed confirmation candle (hammer, bullish pin bar,
   bullish engulfing, morning star; mirror set for bearish)
6. volume confirmation: confirmation-candle futures volume ≥ 1.0 × SMA20 (IMPL)
7. score ≥ 75 (SPEC)
8. not chasing: confirmation range ≤ 1.5 ATR and trigger ≤ 1.5 ATR from EMA9 (IMPL)
9. reward/risk: the next major opposing S/R level beyond the trigger must be ≥ **2.0 R** away (IMPL,
   consistent with T2 = 1:2). With no such level, this check passes.
10. data quality, risk gate (limits, active position, daily loss, kill switch, uncertain orders) and
    the entry cutoff
Entry only on a break of the confirmation high + 1 tick (bull) / low − 1 tick (bear).

**C (breakout and retest).** In the last 6 completed bars (IMPL), a completed candle closed through an
important level (prev_day/OR/swing/zone/OI/trendline) that already existed before it. The previous close was
on the other side, and breakout volume was ≥ **1.5 ×** SMA20 (IMPL). A later candle retested the level
within tolerance with no close back through it. The confirmation candle must then pass the same
mandatory set. Setup A/B takes precedence when both match.

**Signal validity (SPEC):** a signal expires when the 3rd completed 5m candle after the confirmation
candle closes without a trigger. It is cancelled earlier on any of these: a completed close beyond the stop, an opposing confirmed
structure break, the 15m EMA flipping, derivatives confirmation lost, or a data-quality fault.

## Exact 100-point score (SPEC weights; sub-rules are IMPL and documented)
| # | Component | Max | Sub-rules |
|---|---|---|---|
| 1 | EMA alignment | 10 | 15m aligned +5, 5m aligned +5 |
| 2 | VWAP position | 10 | right side and ≤ 1.5 ATR away 10; right side but extended 5; else 0 |
| 3 | 15m structure | 10 | HH and HL (mirror) 10; one of them 5 |
| 4 | S/R confluence | 10 | families at zone excluding fib/trendline: 1 → 4, 2 → 7, ≥3 → 10 |
| 5 | OI bias | 10 | supporting ≥2 and opposing 0 → 10; ≥1 and 0 → 6; supporting > opposing → 3 |
| 6 | PCR | 5 | near-ATM PCR improving (bull) / falling (bear) by > 0.05 → 5; flat with level on the right side of 1 → 2 |
| 7 | Delta/Gamma | 10 | delta in band +6; gamma ≥ 50% of near-ATM max +4 |
| 8 | IV/Theta | 5 | IV ≤ 1.3 × near-ATM median +3; |theta|/premium ≤ 15%/day +2 |
| 9 | Rejection candle | 10 | recognised rejection +7; close location ≥ 0.75 (≤ 0.25 bear) +3 |
| 10 | Volume | 5 | ≥ 1.0 × SMA20 → 5 |
| 11 | Fib/trendline | 5 | fib at zone +3; trendline at zone +2 |
| 12 | Candle formation | 5 | supporting multi-bar formation in the last 6 bars and none opposing → 5 |
| 13 | Max Pain/OI context | 5 | Max Pain on the trade side of spot +3; opposing OI wall ≥ 1R away +2 |

To avoid double counting: fib/trendline appear only in #11; PCR only in #6; the rejection pattern only in #9
(formations come from a separate list); OI support at the entry counts in #4, while #13 measures room to move.
Missing or stale inputs score 0 with the reason "data unavailable". Bands: 85–100 strong, 75–84
valid, 60–74 wait, below 60 avoid. **Failing any mandatory check overrides the score.**

## Option chain (IMPL interpretation rules; SPEC principle "inferred, never certain")
Near-ATM window is ATM ± 3 strikes. The comparison basis is the previous intraday snapshot of the same expiry,
or else the feed's previous-day OI and close. Evidence items (each **INFERRED**):
* put support strengthening: Σ PE OI change (strikes ≤ ATM) > 0 **and** Σ PE premium change < 0 **and** underlying up or flat
* call unwinding: Σ CE OI change (≥ ATM) < 0 **and** CE premium up **and** underlying up
* call resistance strengthening / put unwinding: mirrors of the above
* resistance shifting up / support shifting down: top OI strike moved (intraday basis only)
* long/short build-up: futures price and OI change (when futures OI is available)
OI change alone never produces "present". A missing input makes an item "unavailable".

## Greeks and option selection (SPEC band 0.45–0.65)
Candidates are ATM and one strike ITM. Checks: active expiry, contract id, Greeks available, |delta| in band with the
correct sign, gamma > 0, |theta|/LTP ≤ 15%/day, IV ≤ 1.3 × near-ATM median, volume ≥ 1,000, OI ≥ 1,000,
two-sided quote, spread ≤ 1.5% of mid or ≤ ₹2, LTP > 0 (all IMPL thresholds). The best candidate is the one
with delta closest to 0.55, ties broken by tighter spread. Feed Greeks are preferred; when they are missing,
Black–Scholes values are computed (labelled "calculated") only if price and time inputs exist.

## Stops, targets and exits
* Underlying SL = confirmation low − 0.10 × ATR (bull; IMPL buffer). T1 = 1R, T2 = 2R, T3 = 3R only if the next major
  S/R level is beyond it.
* Premium SL (non-linear): loss ≈ |Δ|R − ½ΓR² + vega × 1 vol point + |θ| × (15/375). It is floored at half
  the delta loss, then spread plus slippage is subtracted. Position size is based on premium risk.
* **Exit control:** whichever comes first, the underlying crossing the technical SL or the option BID reaching the premium SL.
  The Secure-Exit rupee rules also apply.
* T1: book 50% (whole lots). With **1 lot: exit all at T1** (IMPL `single_lot_t1_action`). After T1 the stop
  moves to breakeven plus costs (cost per unit / |Δ|). It then trails at EMA9 ∓ 1.0 × ATR on each completed 5m
  candle and only ever tightens. The remainder exits at T3 if set, otherwise T2.
* An opposing confirmed structure break exits the trade. EOD square-off is at **15:15** and the entry cutoff is **14:45** (IMPL).

## Implementer defaults pending your approval
| Setting | Default | Why |
|---|---|---|
| adx_min_trend | 20 | Common "weak trend" threshold; raise to 25 to be stricter |
| ema_slope_lookback / flat_slope_atr_frac | 3 / 0.05 | Slope measured over 3 bars, normalised by ATR |
| sideways lookback / max EMA crosses / max VWAP crosses | 12 / 2 / 3 | One hour of 5m bars |
| touch / zone tolerance | 0.30 / 0.25 ATR | |
| sl_atr_buffer | 0.10 ATR | Spec says "configurable ATR buffer" without a value |
| max_entry_candle_atr / max_entry_ext_atr | 1.5 / 1.5 | "Do not chase" |
| min_rr_to_next_sr | 2.0 | Matches the T2 minimum of 1:2 |
| volume_confirm_mult / breakout_volume_mult | 1.0 / 1.5 | |
| retest_max_candles (C) | 6 | |
| single_lot_t1_action | exit_all | 50% booking is impossible with one lot |
| trail_atr_mult | 1.0 | |
| option liquidity: min volume / OI / spread | 1000 / 1000 / 1.5% or ₹2 | |
| theta limit / IV spike ratio | 15%/day / 1.30 | |
| entry_cutoff / squareoff | 14:45 / 15:15 | |
| freshness: tick / chain / candle lag | 5 s / 30 s / 90 s | |
| volume source | current-month futures | Index has no volume |
| risk_free_rate (calculated Greeks only) | 6.5% | |
