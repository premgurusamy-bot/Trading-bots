# Options Desk — NIFTY & BANK NIFTY intraday options (Strategy V1.0)

A local, mobile-friendly decision-support and **paper-trading** application for intraday NIFTY / BANK NIFTY option
buying, using Dhan market data. It implements the frozen **Strategy V1.0** (docs/STRATEGY_V1.md) and has these properties:

* **Paper trading by default.** Live trading is disabled and protected by several separate locks.
* No fabricated data. Stale or missing data **blocks new entries**, and the dashboard says why.
* Every decision has a 100-point score breakdown, mandatory checks, and a stored input snapshot for reproducibility.
* Nothing here is investment advice, and **no claim of profitability is made** (see docs/BACKTESTING.md).

---

## 1. Install on Windows (one time)

1. Install **Python 3.11–3.14** (3.13 or 3.14 recommended) from <https://www.python.org/downloads/>. On the first installer screen tick
   **"Add python.exe to PATH"**.
2. Install **Node.js LTS** from <https://nodejs.org/>.
3. Download this project (green **Code → Download ZIP** on GitHub, then unzip; or `git clone`).
4. Open the project folder and **double-click `setup.bat`**. It:
   creates a Python virtual environment → installs packages → builds the dashboard → creates `.env` from
   `.env.example` → initialises the database. It ends with "Setup complete".

## 2. Start / stop

* **Start:** double-click `start.bat`. Your browser opens <http://127.0.0.1:8000>. Keep the black window open.
* **Stop:** close that window, or double-click `stop.bat`. Paper positions, counters and Secure-Exit floors are saved
  and restored on the next start. In LIVE mode, exit positions *before* stopping.
* **Update:** `update.bat` backs up first, pulls the latest version and re-runs setup.
* **Backup:** `backup.bat` copies the database into `backups\`. To restore, stop the app and copy the backup back to
  `backend\data\trading.db`.

## 3. First-run wizard

1. **Create a dashboard password** (at least 8 characters). Every screen and API call requires it.
2. Go to **Settings → Dhan credentials**: enter your Dhan **Client ID** and **Access token**
   (web.dhan.co → My Profile → *DhanHQ Trading APIs* → generate token) and click **Save & reconnect**.
   * They are stored in **Windows Credential Manager**. They are never written to files, the database, logs,
     Git or the browser, and are never shown again (only masked).
   * You need the Dhan **Data APIs** subscription for market data.
3. Open **Health**. Within a minute you should see *WebSocket connected*, *Token ok* and recent tick, chain and candle times.

### When the token expires
Dhan tokens expire (usually daily). The dashboard shows **"token invalid or expired"** and new entries are blocked.
Generate a new token on web.dhan.co and paste it in Settings → Dhan credentials. The feed reconnects, and entries
resume only after fresh data has been validated. Health shows the token's expiry time.

## 4. Android app (recommended way to use it on the phone)

The phone app is an installable **APK**. The trading engine keeps running on your PC, and the app is its remote screen.
Full guide: **docs/ANDROID_APP.md**. Short version:
1. GitHub → **Actions → Android APK** → latest run → download **OptionsDesk-android-apk** → install the APK on the phone.
2. On the PC, run **`start-mobile.bat`**. It prints an address such as `192.168.1.20:8000`.
3. In the app: **Connect to your PC** → enter that address → log in.
4. Away from home: install **Tailscale** (free) on both PC and phone and use the `100.x.x.x` address. Never port-forward.

(The browser version still works: open `http://<PC-IP>:8000` in Chrome on the phone while start-mobile.bat runs.)

## 5. Screens

| Screen | What it shows |
|---|---|
| **Dashboard** | NIFTY/BANK NIFTY LTP and change, session and feed status, data age, 15m/5m trend, VWAP, ADX, setup state (bullish/bearish/sideways/no trade), mandatory checks, 100-point breakdown with reasons, entries today per index, P&L (gross/net/unrealised), balance, capital used, risk status |
| **Chart** | 1m/5m/15m candles, EMA9/20, session VWAP, futures volume, PDH/PDL/PDC, opening range, swings, zones, Fibonacci, OI levels, trendlines, signal markers with trigger/SL/T1–T3, plus an inspectable S/R table |
| **Option chain** | ATM-highlighted strike window, CE/PE OI, ΔOI, volume, IV, bid/ask, LTP, overall and near-ATM PCR, PCR change, Max Pain, OI walls, INFERRED evidence, timestamps and stale warnings |
| **Greeks** | ATM and 1-ITM CE/PE candidates with delta/gamma/theta/vega/IV, spread, volume, lot size, every check and the selection rationale. Feed vs calculated Greeks are labelled |
| **Signals** | Pending/triggered/executed/cancelled/expired, setup, score, trigger, SL, targets, expiry time, invalidation rules, transition history |
| **Trades** | Paper/live label, positions, partial exits, orders and broker-confirmed fills, average prices, gross/charges/net, outcome explanation. Live orders waiting for your confirmation appear here with the full pre-trade summary |
| **Risk** | Daily limits and counters, Secure-Exit floors, cutoff times, **kill switch**, pause entries |
| **Journal** | Execution analytics (win rate, expectancy, profit factor, drawdown, by index/setup/hour) and a **separate** research log of rejected/expired candidates with snapshots. CSV export |
| **Backtest** | Genuine-data technical-only tests with provenance and disclaimers |
| **Settings** | Credentials, mode, strategy parameters (frozen V1.0 values marked and protected), Secure-Exit limits, paper account, charge schedule |
| **Health** | Dhan connectivity, token status, WebSocket, API errors, last data times, database, instrument-master version, system events, audit log |

## 6. Paper trading workflow
Start the app during market hours with credentials saved. The engine evaluates each **completed** 5-minute candle.
Valid setups become **PENDING** signals. A break of the trigger price **TRIGGERS** the signal, and then the
pre-trade checks run again on fresh data, the position is sized, and a paper order fills against the real bid/ask.
Exits follow the V1.0 rules plus the Secure-Exit layer. Review everything in Signals, Trades and Journal.
Details: docs/PAPER_TRADING.md.

## 7. Live trading (disabled — read before ever enabling)
Live orders need **all** of the following, and mode resets to PAPER on every restart:
1. `LIVE_TRADING_FEATURE_FLAG=true` in `.env` (default `false`);
2. a connected Dhan feed, the Trading API permission, and a valid token;
3. Settings → Trading mode: your password plus the typed acknowledgement;
4. for **every** order, your confirmation of a pre-trade summary (instrument, CE/PE, expiry, strike, lots, premium,
   capital, charges, maximum risk, stop, targets). All checks re-run on fresh data before sending.
Positions count as filled or exited only when Dhan reports fills. Unknown order state blocks entries until you
reconcile. The kill switch blocks entries and sends exits. Live mode cannot be activated inside automated tests.
**Run paper mode for a long time before considering this.** See docs/DHAN_INTEGRATION.md for unverified API fields.

## 8. TradingView / Pine Script
`pine/nifty_banknifty_v1_technical.pine` is a Pine Script **v6** indicator covering only the technical rules Pine can compute.
* Add it: TradingView → open the NIFTY (or BANKNIFTY) index chart, 5-minute → **Pine Editor** → paste the file → **Save**
  → **Add to chart**. Set the "Futures symbol" input (e.g. `NSE:NIFTY1!` / `NSE:BANKNIFTY1!`), because the index has no volume.
* It plots EMA9/20, futures-volume VWAP, PDH/PDL/PDC, opening range, confirmed swings, Fibonacci and technical
  candidates "CE?" / "PE?" on **closed bars only** (no repainting; higher-timeframe data uses the previous completed bar).
* Option chain, OI, PCR, Max Pain, Greeks, liquidity, the 75-point score and Setup C are shown as **EXTERNAL**. Pine
  cannot call the Dhan API. Alerts say "technical candidate only — the Python engine must validate".
* TradingView is for viewing only. It is not the decision authority and not a data source for the app.
* The script could not be compiled in the build environment (TradingView only). If TradingView reports an error, send me the message.

## 9. Project layout
```
setup.bat start.bat start-mobile.bat stop.bat update.bat backup.bat .env.example
.github/workflows/android-apk.yml   builds the Android APK
backend/
  app/strategy/   indicators, candles, structure, levels, patterns, optionchain, greeks, selection,
                  scoring, setups, signals, freshness, engine, params (V1.0)
  app/risk/       charges, sizing, limits, secure_exit, exits
  app/execution/  broker_base, paper (default), dhan_live (disabled)
  app/data/       provider interface, dhan/ client, parsers, feed
  app/core/       service (orchestration, persistence, recovery), secrets, types
  app/backtest/   sim, runner, metrics
  app/api/        auth, routes          app/main.py  app/runtime.py  app/db.py
  alembic/        migrations            tests/  (pytest, synthetic fixtures)
frontend/         React + TypeScript + Vite + Tailwind PWA; frontend/android = Capacitor Android app
pine/             Pine Script v6 technical companion
docs/             AUDIT, STRATEGY_V1, PATTERNS, SECURE_EXIT, DHAN_INTEGRATION, CHARGES,
                  PAPER_TRADING, BACKTESTING, TROUBLESHOOTING, TEST_REPORT
```

## 10. Developers
```
cd backend && .venv\Scripts\python -m pytest -q          # backend tests
cd frontend && npm test && npm run build                  # frontend tests and build
cd frontend && npm run dev                                # UI dev server (proxies to :8000)
```
PostgreSQL: set `DATABASE_URL=postgresql+psycopg://…` and install a driver; the migrations are database-agnostic.

## Security notes
Secrets live only in Windows Credential Manager (or, as a fallback, the git-ignored `.env`). The dashboard needs a
password (PBKDF2, 600k iterations) and the login is rate-limited. API responses are `no-store`. Log lines that look like they contain tokens
are redacted. Settings, mode, credential and live-enablement changes are audit-logged. The app listens on localhost by default.
