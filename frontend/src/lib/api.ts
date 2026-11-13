// API client. Holds ONLY the dashboard session token (sessionStorage). Dhan credentials never reach the browser.
const TOKEN_KEY = "desk_session";

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(t: string | null) {
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: session lasts for this page only */
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, msg: string) {
    super(msg);
    this.status = status;
  }
}

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const tok = getToken();
  if (tok) headers.Authorization = `Bearer ${tok}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(path, { method: opts.method || "GET", headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  if (r.status === 401 && !path.startsWith("/api/auth")) {
    setToken(null);
    onUnauthorized();
  }
  if (!r.ok) {
    let msg = `${r.status}`;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* ignore */
    }
    throw new ApiError(r.status, msg);
  }
  const ct = r.headers.get("content-type") || "";
  return (ct.includes("json") ? r.json() : (r.text() as any)) as Promise<T>;
}

export type WsEvent = { kind: string; at?: string; [k: string]: any };

/** WebSocket with heartbeat watchdog and exponential-backoff reconnect. */
export function connectEvents(onEvent: (e: WsEvent) => void, onState: (s: "open" | "closed") => void) {
  let ws: WebSocket | null = null;
  let stopped = false;
  let backoff = 1000;
  let watchdog: number | undefined;
  const arm = () => {
    window.clearTimeout(watchdog);
    watchdog = window.setTimeout(() => ws?.close(), 25000); // server heartbeats every 10s
  };
  const open = () => {
    const tok = getToken();
    if (!tok || stopped) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(tok)}`);
    ws.onopen = () => {
      backoff = 1000;
      onState("open");
      arm();
    };
    ws.onmessage = (m) => {
      arm();
      try {
        const e = JSON.parse(m.data);
        if (e.kind !== "heartbeat") onEvent(e);
      } catch {
        /* ignore malformed */
      }
    };
    ws.onclose = () => {
      onState("closed");
      window.clearTimeout(watchdog);
      if (!stopped) window.setTimeout(open, (backoff = Math.min(backoff * 2, 30000)));
    };
  };
  open();
  return () => {
    stopped = true;
    window.clearTimeout(watchdog);
    ws?.close();
  };
}
