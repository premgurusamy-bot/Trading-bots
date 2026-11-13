# Backtesting

**No backtest results are included in this delivery.** The build environment had no Dhan access, and running the
strategy on synthetic data would prove nothing about markets. Synthetic data is used only in unit tests.

## What can be tested with genuine data
| Data | Available from Dhan? | Used |
|---|---|---|
| Index 5m candles | yes (`/charts/intraday`, chunked ~85 days per request) | yes |
| Futures volume | only for contracts still listed; expired futures are not fetched | partial, coverage reported |
| ATM option premiums (expired contracts) | `/charts/rollingoption` (ATM CE/PE OHLC, IV, OI, spot) | premium overlay for rupee P&L |
| Full historical option chain (all strikes' OI, PCR, Max Pain, Greeks, bid/ask) | **no documented endpoint** | **not available** |

So every run from the Backtest page is labelled **technical_only** (or **technical_with_premium_overlay**). These
runs skip the derivatives, Greeks and liquidity checks and the 75-point gate. They are **not** a full-strategy
backtest and cannot show that the strategy is profitable. A full backtest needs genuine timestamped option-chain
snapshots. The live app stores every chain it receives (`chain_snapshots` table), so you will accumulate your own
genuine history to replay later.

## Safeguards
* No look-ahead: each bar is evaluated on data up to that bar only, and the forming 15m candle is excluded.
  A test checks that appending future data never changes earlier results.
* Fills: trigger at max(trigger, bar open) + slippage. If a bar touches both the stop and a target, the **stop is assumed first**.
* Costs come from the effective-dated charge schedule (premium overlay only). R-multiples are reported when premiums are missing.
* Periods are split by date: 60% development, 20% validation, 20% out-of-sample. **No parameters are optimised** on any of them.
* Reports include trade count, win rate, average win/loss, expectancy, profit factor, max drawdown, net, costs, R distribution,
  breakdowns by index, setup and hour, losing streaks and data coverage. Fewer than 30 trades is flagged as inadequate.
* Survivorship: index data has no survivorship issue. Today's lot size is applied to all past periods, which is a stated limitation.
* Every run is stored with its config, strategy version, parameters, charge schedule and data SHA-256 (`backtest_runs`).

If two years of genuine intraday data are available to your account, test that period. Otherwise the page reports the coverage it actually received.
