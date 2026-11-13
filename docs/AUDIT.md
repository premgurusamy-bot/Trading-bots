# Phase 1 Audit — 2026-11-13

## Findings
* Repository `premgurusamy-bot/Trading-bots`: **empty**. No commits on any branch, no remote refs.
* Not found: existing dashboard, Excel/VBA workbooks, Dhan API modules, Pine Script, bots,
  databases, or a Secure Exit / profit-protection implementation.
* Consequence: nothing had to be preserved or migrated. The Secure Exit Rules are implemented
  from the settings in the master prompt (no pre-existing code to compare against, so there are
  no discrepancies to report).
* Checkpoint: this audit plus `.gitignore` is the initial Git commit, made before any code was written.

## Verification sources available in the build environment
* Official DhanHQ Python SDK `dhanhq==2.3.0` (PyPI, © Dhan), read for endpoint paths, headers,
  request bodies and WebSocket packet layouts. See `docs/DHAN_INTEGRATION.md`.
* `dhanhq.co`, `api.dhan.co` and `images.dhan.co` were **blocked** by the build sandbox's network
  policy, so the official web docs and the live API could not be fetched. Response schemas
  that the SDK does not show are marked **UNVERIFIED** in the code and docs. Parsers
  reject responses that lack required fields instead of guessing.

## Data gaps identified
* Index spot (IDX_I) has no traded volume. Volume and VWAP use current-month index **futures**
  volume (configurable). See docs/STRATEGY_V1.md §Data.
* No historical option-chain / OI / Greeks history is available from the documented endpoints except
  the expired-options "rolling option" endpoint (OHLC, IV, OI, spot per relative strike).
  Full-strategy backtests are therefore limited. See docs/BACKTESTING.md.
