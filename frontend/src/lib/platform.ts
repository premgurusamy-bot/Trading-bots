// Platform helpers: native Android app (Capacitor) vs browser/PWA.
import { Capacitor } from "@capacitor/core";

const SERVER_KEY = "desk_server";
const NOTIFY_KEY = "desk_notify";

export const isNative = (): boolean => {
  try {
    return Capacitor.isNativePlatform();
  } catch {
    return false;
  }
};

/** Normalise user input like "192.168.1.20:8000" or "http://100.64.1.2:8000/" to "http://host:port". */
export function normaliseServer(input: string): string | null {
  let s = input.trim();
  if (!s) return null;
  if (!/^https?:\/\//i.test(s)) s = "http://" + s;
  try {
    const u = new URL(s);
    if (!u.hostname) return null;
    if (!u.port && u.protocol === "http:") u.port = "8000";
    return `${u.protocol}//${u.host}`;
  } catch {
    return null;
  }
}

/** Base URL for API calls: "" (same origin) in the browser, the saved PC address in the app. */
export function serverBase(): string {
  if (!isNative()) return "";
  try {
    return localStorage.getItem(SERVER_KEY) || "";
  } catch {
    return "";
  }
}

export function setServerBase(url: string | null) {
  try {
    if (url) localStorage.setItem(SERVER_KEY, url);
    else localStorage.removeItem(SERVER_KEY);
  } catch {
    /* ignore */
  }
}

export function needsServerSetup(): boolean {
  return isNative() && !serverBase();
}

export function notificationsEnabled(): boolean {
  try {
    return localStorage.getItem(NOTIFY_KEY) !== "off";
  } catch {
    return true;
  }
}

export function setNotificationsEnabled(on: boolean) {
  try {
    localStorage.setItem(NOTIFY_KEY, on ? "on" : "off");
  } catch {
    /* ignore */
  }
}

let notifId = 1;
let permAsked = false;

/** Phone notification (native app only). Delivered while the app is open or recently backgrounded;
 * Android may suspend the app later, so this is NOT a guaranteed alert channel. */
export async function notify(title: string, body: string) {
  if (!isNative() || !notificationsEnabled()) return;
  try {
    const { LocalNotifications } = await import("@capacitor/local-notifications");
    if (!permAsked) {
      permAsked = true;
      const p = await LocalNotifications.checkPermissions();
      if (p.display !== "granted") await LocalNotifications.requestPermissions();
    }
    await LocalNotifications.schedule({ notifications: [{ id: notifId++, title, body: body.slice(0, 200) }] });
  } catch {
    /* notifications unavailable: in-app toast still shows */
  }
}

/** Android hardware back button: go back in the app, or minimise at the root. */
export async function installBackButton() {
  if (!isNative()) return;
  try {
    const { App } = await import("@capacitor/app");
    App.addListener("backButton", ({ canGoBack }) => {
      if (canGoBack && window.location.pathname !== "/") window.history.back();
      else App.minimizeApp();
    });
  } catch {
    /* ignore */
  }
}
