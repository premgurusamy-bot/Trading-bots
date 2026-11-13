import type { CapacitorConfig } from "@capacitor/cli";

// Android app shell. The trading engine runs on your Windows PC; the app connects to it over
// Wi-Fi or Tailscale (address entered on first launch). "http" scheme + cleartext lets the app reach
// http://<PC-IP>:8000 on your private network. Use Tailscale for encryption when away from home.
const config: CapacitorConfig = {
  appId: "in.optionsdesk.app",
  appName: "Options Desk",
  webDir: "dist",
  server: { androidScheme: "http", cleartext: true },
  android: { allowMixedContent: false, backgroundColor: "#0f172a" },
  plugins: {
    LocalNotifications: { smallIcon: "ic_stat_notify", iconColor: "#22c55e" },
  },
};

export default config;
