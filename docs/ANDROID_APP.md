# Options Desk Android app

## How it works
```
 Android phone (Options Desk app)  ──Wi-Fi or Tailscale──►  Windows PC (start-mobile.bat)
   screens, alerts, confirmations                            trading engine + Dhan connection + database
```
The **engine runs on your Windows PC**. The phone app is its remote control and screen. Android stops
background apps to save battery, so a phone cannot reliably keep watching prices and managing exits.
Your PC can. If the phone app closes, the PC keeps managing paper positions, stops and square-off.
If the **PC** is off, nothing runs and the app shows "Cannot reach your PC".

## 1. Get the APK (built automatically by GitHub)
Every push that changes the app builds a new APK on GitHub Actions.
1. On a PC browser, open your repository on GitHub → **Actions** → **Android APK** → the latest green run.
2. Under **Artifacts**, download **OptionsDesk-android-apk** (a .zip), then unzip it to get `OptionsDesk-1.0.N.apk`.
3. Copy the APK to the phone (USB, Google Drive, or email it to yourself).
4. On the phone, tap the APK. Android asks to allow installing from this source (Files/Drive). Allow it, then tap **Install**.
   Play Protect may warn about an unknown developer. This is expected for a personal app that isn't on the Play Store.
5. Later versions install over the old one (same signing key), and your settings are kept.

To build again without code changes: Actions → Android APK → **Run workflow**.

## 2. Start the engine for the phone
On the PC, double-click **`start-mobile.bat`** instead of `start.bat`. It:
* lets other devices on your network connect (login is still required for everything),
* prints the address to type into the app, e.g. `192.168.1.20:8000`.

The first time, Windows Firewall asks about Python. Tick **Private networks** only and click Allow.

## 3. Connect the app
Open **Options Desk** on the phone → **Connect to your PC** → type the address → **Connect** → log in with your
dashboard password. To change the PC later: **More → Change PC address**.

## 4. Use it outside home (optional, free): Tailscale
1. Install **Tailscale** on the PC (tailscale.com/download) and on the phone (Play Store) and sign in with the same account on both.
2. `start-mobile.bat` then also shows a `100.x.x.x:8000` address. Use that in the app, and it works over mobile data.
3. Tailscale encrypts the connection (WireGuard). **Never** port-forward port 8000 on your router.

On plain home Wi-Fi the connection is unencrypted HTTP inside your own network. Anyone on that Wi-Fi could
see traffic, including the session token (not your Dhan token, which never leaves the PC). For full
encryption use Tailscale even at home.

## 5. Notifications
Settings → Notifications → enable, then allow the permission. You get alerts for new, triggered and executed
signals, closed trades, live-order confirmations and system warnings **while the app is open or recently in the
background**. Android may pause the app later, so these alerts are **not guaranteed**. Never rely on them alone
for risk management. The engine on the PC enforces all stops and limits regardless.

## 6. Phone-specific behaviour
* Bottom tabs: Home, Chart, Signals, Trades, More (Option chain, Greeks, Risk, Journal, Backtest, Settings, Health).
* The Android back button goes back a screen, or minimises the app on Home.
* The login lasts until Android closes the app. You then log in again (the token is kept in memory only).
* The kill switch, live confirmations and every setting work the same as on the PC.

## Technical notes
* Built with Capacitor 6 (`frontend/android`, `frontend/capacitor.config.ts`). The app loads the dashboard bundled
  inside the APK and calls the PC API. The backend accepts only the app's `http://localhost` origin for
  cross-origin calls (CORS), and every call still needs the login token.
* Signing: a fixed **debug** key (`frontend/android/app/debug.keystore`, standard password `android`) is committed so
  updates install over each other. It is not a secret and only identifies this sideloaded app. For Play Store
  distribution you would create a private release key stored in GitHub Secrets.
* Build locally (optional; needs Android Studio): `cd frontend && npm run build && npx cap sync android && npx cap open android`.
