# Troubleshooting

| Symptom | Fix |
|---|---|
| `setup.bat`: "Python launcher py not found" | Install Python 3.11+ from python.org and tick **Add python.exe to PATH**. Close and reopen the folder, then run setup.bat again. |
| `setup.bat`: "Node.js not found" | Install Node.js LTS from nodejs.org, then rerun. |
| `Failed building wheel for pydantic-core` / `link.exe was not found` / Rust or Visual Studio messages | pip tried to compile a package because no ready-made Windows package matched your Python. Fixed: the app now pins versions with Windows packages for Python 3.11–3.14 and setup.bat installs ready-made packages only. Get the latest version of the app and run setup.bat again. Never install Rust or Visual Studio for this. |
| pip errors behind a proxy/antivirus | Run `backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt` manually to see the error; allow pypi.org. |
| `npm ci` fails | Delete `frontend\node_modules` and rerun setup.bat. Check your internet connection. |
| Browser says "can't connect" | start.bat must stay open. Check that its window shows no error. Port 8000 busy → change `APP_PORT` in `.env`, or run stop.bat. |
| "Address already in use" | Another copy is running. Run stop.bat. |
| Dashboard: "Dhan credentials not configured" | Settings → Dhan credentials → enter client ID and access token. |
| "token invalid or expired" | Generate a new access token on web.dhan.co (My Profile → DhanHQ Trading APIs) and save it in Settings. Entries stay blocked until fresh data arrives. |
| "Market feed WebSocket disconnected" | The app reconnects automatically with back-off. Check internet/firewall (allow `api-feed.dhan.co`) and the Data API subscription. Entries are blocked while disconnected. |
| "Dhan rate limit active" | The app pauses for 60 s by itself. Don't run other Dhan API scripts at the same time. |
| API errors like DH-902 (not subscribed) | Your account lacks the Data/Trading API permission. Enable it in Dhan. |
| Option chain "STALE" / "chain N s old" | The poll failed or the market is closed. See System Health → Recent errors. Stale data never triggers entries. |
| "No completed 5m candles" / candle lag | Normal before 09:20 and after the close. During market hours, check the intraday API errors in Health. |
| Instrument master failed to load | Health shows the reason. Check that `images.dhan.co` is reachable, then click "Reconnect feed". |
| Parse error mentioning a field | Dhan changed or differs from the expected schema. Entries stay blocked. Report the message; don't edit the data. |
| Phone can't open the dashboard | Set `APP_HOST=0.0.0.0` in `.env`, restart, allow Python through Windows Firewall (Private network) and open `http://<PC-IP>:8000` on the same Wi-Fi. Find the IP with `ipconfig`. |
| "Order state uncertain" | Check orders and positions in the Dhan app, then Risk → Clear uncertain state (typed confirmation). |
| Lost password | Stop the app, back up `backend\data\trading.db`, then run `backend\.venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect(r'backend\data\trading.db');c.execute(\"delete from settings where key='auth'\");c.commit()"` and start again to set a new password. |
| Restore a backup | Stop the app and copy `backups\trading-<date>.db` to `backend\data\trading.db`. |
