# Charges and costs

Code: `backend/app/risk/charges.py`. Editable in **Settings → Charge schedule** (audited). Every trade journal stores
the schedule version and effective date it used. Gross P&L and net P&L (after charges) are always shown separately.

Default schedule (index options, per leg). These are **maintained defaults, not authoritative**. Verify them before relying on net P&L:

| Charge | Default | Applies to | Verify at |
|---|---|---|---|
| Brokerage | ₹20 per executed order | each order | Dhan pricing page |
| STT | 0.10% of premium until 2026-03-31; **0.15% from 2026-04-01** (Union Budget 2026-27) | SELL side | Income-tax / NSE circulars |
| Exchange transaction charge | 0.03503% of premium | both | NSE circular (rates effective 2024-10-01) |
| SEBI turnover fee | ₹10 per crore | both | SEBI |
| Stamp duty | 0.003% of premium | BUY side | Indian Stamp Act notification |
| GST | 18% of (brokerage + exchange charge + SEBI fee) | both | |
| IPFT | 0 (configurable) | both | NSE |

The STT change was found by web research on 2026-11-13 (reports of Budget 2026-27, e.g. Upstox and Angel One articles).
The official notification could not be fetched from the build environment. Lot sizes are **not** in this schedule:
they are always read from the current Dhan instrument master (NIFTY 65 / BANKNIFTY 30 per Jan-2026 reports, never hard-coded).

Slippage is modelled separately: entries fill at ASK + 2 ticks, exits at BID − 2 ticks (paper). Spread is also part of the premium-SL buffer.
