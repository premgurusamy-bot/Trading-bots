# Dhan integration — what is verified and what is not

**Verification source:** the official DhanHQ Python SDK `dhanhq==2.3.0` (PyPI, © Dhan), read line by line.
The build sandbox **blocked** `dhanhq.co`, `api.dhan.co` and `images.dhan.co`, so neither the web documentation
nor the live API could be checked, and **no request has ever been sent to Dhan from this code**.

## Verified against the official SDK
| Item | Detail |
|---|---|
| REST base / auth headers | `https://api.dhan.co/v2`, headers `access-token`, `client-id` |
| Option chain | `POST /optionchain {UnderlyingScrip, UnderlyingSeg, Expiry}` |
| Expiry list | `POST /optionchain/expirylist {UnderlyingScrip, UnderlyingSeg}` |
| Intraday candles | `POST /charts/intraday {securityId, exchangeSegment, instrument, interval(1/5/15/25/60), oi, fromDate, toDate}` |
| Historical daily | `POST /charts/historical {..., expiryCode, oi, ...}` |
| Expired options | `POST /charts/rollingoption {securityId, exchangeSegment, instrument, expiryFlag(WEEK/MONTH), expiryCode, strike("ATM"…), drvOptionType(CALL/PUT), requiredData[...], fromDate, toDate, interval}` |
| Quotes | `POST /marketfeed/ltp|ohlc|quote {"SEG":[ids]}` |
| Orders | `POST /orders` body fields; `GET /orders/{id}`; `GET /orders/external/{correlationId}`; `DELETE /orders/{id}` |
| Funds/positions/profile | `GET /fundlimit`, `GET /positions`, `GET /profile` |
| Token renewal | `GET /RenewToken` (headers access-token, dhanClientId) — exposed only as a manual flow, not automated |
| WebSocket v2 | `wss://api-feed.dhan.co?version=2&token=…&clientId=…&authType=2`; JSON subscribe `{RequestCode 15/17/21, InstrumentCount, InstrumentList[{ExchangeSegment, SecurityId}]}` (max 100 per message); binary packet layouts for ticker(2), quote(4), OI(5), prev close(6), full(8) with 5-level depth, and disconnect(50) |
| Instrument master URLs | `api-scrip-master.csv` (compact) and `api-scrip-master-detailed.csv` |
| Error body | `errorCode`, `errorType`, `errorMessage` |

## UNVERIFIED — must be checked on first live run
* Option-chain response JSON (`data.last_price`, `data.oc["<strike>"].ce/pe` with `greeks{delta,theta,gamma,vega}`,
  `implied_volatility`, `last_price`, `oi`, `previous_oi`, `previous_close_price`, `volume`, `security_id`,
  `top_bid/ask_price/quantity`). The parser **rejects** payloads that lack `data.oc` or `last_price`.
* Chart arrays (`open, high, low, close, volume, timestamp` as epoch seconds, `open_interest`).
* Compact master column names (`SEM_*`). The parser fails loudly if any required column is missing.
* NIFTY/BANKNIFTY index security ids are resolved from the master (INDEX rows) at runtime, never hard-coded.
* Order status values (TRANSIT/PENDING/PART_TRADED/TRADED/REJECTED/CANCELLED/EXPIRED) and fields
  `orderId, orderStatus, filledQty, averageTradedPrice`; `/fundlimit` key `availabelBalance` (sic). Any unknown status
  becomes UNKNOWN, which blocks entries until you reconcile.
* Rate limits. The client spaces calls conservatively (option chain ≥ 3 s apart), backs off 60 s after HTTP 429 or DH-904,
  and stops calling after 401/403 or DH-901 until the token is replaced.
* Rolling-option response shape (used only for the backtest premium overlay). If it doesn't match, the overlay is
  skipped and the reason is reported.

**How to verify:** with valid credentials and the market open, check System Health. Feed connected, chain
received and candles received means the shapes match. Any parse error appears in "Recent feed/API errors"
with the field that failed.

## Account permissions you need
* **Data APIs** subscription (market quote, option chain, historical, live feed) for market data.
* **Trading APIs** for live orders (optional; live trading is disabled by default).
* Missing permissions show up as API errors on System Health. The app does not pretend the integration works.

## Token handling
Dhan access tokens expire (typically within 24 h). The app reads the JWT `exp` claim for display and warning
only. When the token expires, Dhan returns 401, the client stops calling, the dashboard shows
"token invalid or expired", and **new entries are blocked**. Generate a new token at web.dhan.co → My Profile →
DhanHQ Trading APIs and paste it in **Settings → Dhan credentials**. The feed then reconnects and fresh data is
validated before entries resume. Tokens are stored in Windows Credential Manager and never in the database,
logs, frontend or Git.
