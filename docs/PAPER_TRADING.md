# Paper trading

Paper mode is the default and the mode the app always starts in. No real order is ever placed in paper mode.

## Fill model (conservative)
* Orders can fill only on an option quote **received after** placement plus 0.5 s latency, and each quote is used once.
* BUY fills at ASK + 2 ticks and SELL at BID − 2 ticks. Stops and targets never fill at the exact trigger price.
* Displayed depth (ask/bid quantity) caps each fill, so large orders fill **partially** across quotes. An entry still
  partial after 10 s has its remainder cancelled and the position size becomes the filled quantity.
* With no fresh two-sided quote for 10 s, the order is **rejected**. A rejected entry cancels the signal and does
  **not** count as a trade. An entry that filled and was later stopped out **does** count.
* Exit rejections are retried up to 3 times; after that the order state becomes UNCERTAIN and entries are blocked.
* Charges are deducted from the paper balance when the trade closes.

## Everything enforced exactly as in live
Per-index daily limit (2), one active position per index, no re-entry or duplicate execution, daily loss stop, entry
cutoff, EOD square-off, sizing, premium/technical SL, T1 partial booking, trailing and the Secure-Exit floors.

## Persistence and restart
Signals, trades, orders, fills, daily counters, Secure-Exit floors and paper cash are written to SQLite on every
change. After a restart, open positions resume with their stops and floors. A paper order that was still working at
crash time is marked "lost at restart (not executed)", because paper orders exist only inside the running process.

## Reset
Settings → Paper account → type `RESET PAPER ACCOUNT`. History is kept and the reset is written to the audit log.

## Inspecting
* **Signals**: every pending, triggered, executed, cancelled or expired signal, with its score breakdown,
  mandatory checks, plan, invalidation rules and transition history.
* **Trades**: orders and fills, gross/charges/net, slippage, premium high/low after entry, Secure-Exit floor, and an
  outcome explanation built from recorded prices.
* **Journal → Research log**: rejected, cancelled and expired candidates with their failing conditions, the premium
  high/low after the signal, and the decision snapshot. These are kept out of the execution analytics.
