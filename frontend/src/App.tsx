import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, connectEvents, getToken, setToken, setUnauthorizedHandler, WsEvent } from "./lib/api";
import { Badge, ErrorBox } from "./components/ui";
import { isNative, needsServerSetup, normaliseServer, notify, serverBase, setServerBase } from "./lib/platform";
import Dashboard from "./pages/Dashboard";
import ChartPage from "./pages/ChartPage";
import ChainPage from "./pages/ChainPage";
import GreeksPage from "./pages/GreeksPage";
import SignalsPage from "./pages/SignalsPage";
import TradesPage from "./pages/TradesPage";
import RiskPage from "./pages/RiskPage";
import JournalPage from "./pages/JournalPage";
import BacktestPage from "./pages/BacktestPage";
import SettingsPage from "./pages/SettingsPage";
import HealthPage from "./pages/HealthPage";

const NAV = [
  ["/", "Dashboard"],
  ["/chart", "Chart"],
  ["/chain", "Option chain"],
  ["/greeks", "Greeks"],
  ["/signals", "Signals"],
  ["/trades", "Trades"],
  ["/risk", "Risk"],
  ["/journal", "Journal"],
  ["/backtest", "Backtest"],
  ["/settings", "Settings"],
  ["/health", "Health"],
];

const TABS: [string, string, string][] = [
  ["/", "Home", "◉"],
  ["/chart", "Chart", "📈"],
  ["/signals", "Signals", "⚡"],
  ["/trades", "Trades", "₹"],
];

/** Short phone notification text for important engine events (null = don't notify). */
export function phoneNotice(e: WsEvent): [string, string] | null {
  if (e.kind === "signal" && ["PENDING", "TRIGGERED", "EXECUTED"].includes(e.state))
    return [`${e.index} ${e.direction === "BULLISH" ? "CE" : "PE"} signal ${e.state}`, `Setup ${e.setup} · score ${e.score?.total ?? "?"} · trigger ${e.plan?.trigger ?? "?"}`];
  if (e.kind === "confirm_required") return ["LIVE order needs your confirmation", "Open Trades to review the pre-trade summary."];
  if (e.kind === "trade" && e.state === "CLOSED") return ["Trade closed", `Net ₹${e.net ?? "?"} (gross ₹${e.gross ?? "?"})`];
  if (e.kind === "trade" && e.state === "ENTRY_FAILED") return ["Entry failed", String(e.reason ?? "")];
  if (e.kind === "system" && ["ERROR", "WARN"].includes(e.level)) return [`System ${e.level}`, String(e.message ?? "")];
  return null;
}

function ConnectScreen({ onDone }: { onDone: () => void }) {
  const [addr, setAddr] = useState(serverBase().replace(/^https?:\/\//, ""));
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const connect = async () => {
    setErr(null);
    const url = normaliseServer(addr);
    if (!url) return setErr(new Error("Enter the PC address, e.g. 192.168.1.20:8000"));
    setBusy(true);
    try {
      const ctl = new AbortController();
      const t = window.setTimeout(() => ctl.abort(), 6000);
      const r = await fetch(url + "/api/health", { signal: ctl.signal });
      window.clearTimeout(t);
      const h = await r.json();
      if (!h || !h.app_version) throw new Error("That address answered, but it is not Options Desk.");
      setServerBase(url);
      setToken(null);
      onDone();
    } catch (e: any) {
      setErr(new Error(e?.message?.includes("Options Desk") ? e.message
        : `Could not reach ${url}. On the PC run start-mobile.bat, keep the phone on the same Wi-Fi (or turn Tailscale on), and allow Python in Windows Firewall.`));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mx-auto mt-10 max-w-sm p-4" style={{ paddingTop: "env(safe-area-inset-top)" }}>
      <div className="card space-y-3">
        <h1 className="text-lg font-bold">Connect to your PC</h1>
        <p className="text-sm">The trading engine runs on your Windows PC. This app is its remote screen.</p>
        <ol className="muted list-decimal space-y-1 pl-5 text-sm">
          <li>On the PC, double-click <b>start-mobile.bat</b>.</li>
          <li>It shows the address to type here, e.g. <b>192.168.1.20:8000</b> (home Wi-Fi) or a <b>100.x.x.x:8000</b> Tailscale address (anywhere).</li>
        </ol>
        <form onSubmit={(e) => { e.preventDefault(); connect(); }} className="space-y-2">
          <input className="input" inputMode="url" autoCapitalize="none" autoCorrect="off" placeholder="192.168.1.20:8000" value={addr} onChange={(e) => setAddr(e.target.value)} autoFocus />
          <ErrorBox error={err} />
          <button className="btn-primary w-full" type="submit" disabled={busy}>{busy ? "Connecting…" : "Connect"}</button>
        </form>
      </div>
    </div>
  );
}

function Login({ onDone }: { onDone: () => void }) {
  const [state, setState] = useState<{ password_set: boolean } | null>(null);
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    api("/api/auth/state").then(setState).catch(setErr);
  }, []);
  const submit = async () => {
    setErr(null);
    try {
      if (!state?.password_set) {
        if (pw !== pw2) throw new Error("Passwords do not match");
        await api("/api/auth/setup", { method: "POST", body: { password: pw } });
      }
      const r = await api<{ token: string }>("/api/auth/login", { method: "POST", body: { password: pw } });
      setToken(r.token);
      onDone();
    } catch (e) {
      setErr(e);
    }
  };
  if (!state) return (
    <div className="space-y-2 p-6">
      <ErrorBox error={err} />
      {isNative() && <button className="btn-ghost w-full" onClick={() => { setServerBase(null); location.reload(); }}>Change PC address</button>}
    </div>
  );
  return (
    <div className="mx-auto mt-10 max-w-sm p-4">
      <div className="card">
        <h1 className="mb-1 text-lg font-bold">Index Options Desk</h1>
        {!state.password_set ? (
          <div className="mb-3 space-y-2 text-sm">
            <p><b>First-run setup — step 1 of 2:</b> create a dashboard password. It protects the app, especially when you open it from your phone.</p>
            <p className="muted">Step 2: after login, open <b>Settings → Dhan credentials</b>. The app starts in <b>PAPER</b> mode; live trading stays disabled.</p>
          </div>
        ) : (
          <p className="muted mb-3 text-sm">Log in to continue.</p>
        )}
        <form onSubmit={(e) => { e.preventDefault(); submit(); }} className="space-y-2">
          <input className="input" type="password" placeholder="Password (min 8 characters)" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus />
          {!state.password_set && <input className="input" type="password" placeholder="Repeat password" value={pw2} onChange={(e) => setPw2(e.target.value)} />}
          <ErrorBox error={err} />
          <button className="btn-primary w-full" type="submit">{state.password_set ? "Log in" : "Create password & log in"}</button>
        </form>
        {isNative() && <button className="btn-ghost mt-2 w-full" onClick={() => { setServerBase(null); location.reload(); }}>Change PC address ({serverBase().replace(/^https?:\/\//, "")})</button>}
      </div>
    </div>
  );
}

export default function App() {
  const [serverReady, setServerReady] = useState(!needsServerSetup());
  const [authed, setAuthed] = useState(!!getToken());
  const [mode, setMode] = useState<string>("PAPER");
  const [wsState, setWsState] = useState<"open" | "closed">("closed");
  const [toast, setToast] = useState<WsEvent | null>(null);
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => setUnauthorizedHandler(() => setAuthed(false)), []);
  useEffect(() => {
    if (!authed) return;
    const stop = connectEvents((e) => {
      if (["signal", "trade", "confirm_required", "system"].includes(e.kind)) {
        setToast(e);
        window.setTimeout(() => setToast(null), 6000);
        const n = phoneNotice(e);
        if (n) notify(n[0], n[1]);
      }
    }, setWsState);
    const id = window.setInterval(() => api("/api/health").then((h) => setMode(h.mode)).catch(() => {}), 5000);
    api("/api/health").then((h) => setMode(h.mode)).catch(() => {});
    return () => {
      stop();
      window.clearInterval(id);
    };
  }, [authed]);

  if (!serverReady) return <ConnectScreen onDone={() => setServerReady(true)} />;
  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  return (
    <div className="min-h-screen">
      <header style={{ paddingTop: "env(safe-area-inset-top)" }} className={`sticky top-0 z-40 border-b ${mode === "LIVE" ? "border-red-700 bg-red-700 text-white" : "border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-900/90"}`}>
        <div className="mx-auto flex max-w-7xl items-center gap-2 px-3 py-2">
          <span className="font-bold">Options Desk</span>
          <Badge tone={mode === "LIVE" ? "red" : "blue"}>{mode === "LIVE" ? "LIVE — REAL MONEY" : "PAPER"}</Badge>
          <Badge tone={wsState === "open" ? "green" : "amber"}>{wsState === "open" ? "events ●" : "events ○"}</Badge>
          <div className="flex-1" />
          <button className="btn-ghost px-2 text-xs" onClick={() => { api("/api/auth/logout", { method: "POST" }).catch(() => {}); setToken(null); setAuthed(false); }}>Log out</button>
        </div>
        <nav className="mx-auto hidden max-w-7xl px-2 pb-2 md:block">
          <div className="flex flex-col gap-1 md:flex-row md:flex-wrap">
            {NAV.map(([to, label]) => (
              <NavLink key={to} to={to} end={to === "/"} onClick={() => setNavOpen(false)}
                className={({ isActive }) => `rounded-md px-3 py-2 text-sm ${isActive ? "bg-blue-600 text-white" : "hover:bg-slate-200 dark:hover:bg-slate-800"}`}>
                {label}
              </NavLink>
            ))}
          </div>
        </nav>
      </header>
      {toast && (
        <div className="fixed bottom-20 left-3 right-3 z-50 md:bottom-4 md:left-auto md:right-4 max-w-sm rounded-lg bg-slate-900 p-3 text-sm text-white shadow-lg">
          <b>{toast.kind}</b>: {toast.message || toast.state || toast.id || toast.trade_id || ""}
        </div>
      )}
      {navOpen && (
        <div className="fixed inset-0 z-40 bg-black/40 md:hidden" onClick={() => setNavOpen(false)}>
          <div className="absolute bottom-16 left-2 right-2 rounded-xl bg-white p-2 shadow-xl dark:bg-slate-900" onClick={(e) => e.stopPropagation()}>
            <div className="grid grid-cols-2 gap-1">
              {NAV.filter(([to]) => !TABS.some(([t]) => t === to)).map(([to, label]) => (
                <NavLink key={to} to={to} onClick={() => setNavOpen(false)}
                  className={({ isActive }) => `rounded-lg px-3 py-3 text-sm ${isActive ? "bg-blue-600 text-white" : "bg-slate-100 dark:bg-slate-800"}`}>{label}</NavLink>
              ))}
              {isNative() && (
                <button className="col-span-2 rounded-lg bg-slate-100 px-3 py-3 text-left text-sm dark:bg-slate-800"
                  onClick={() => { setServerBase(null); setToken(null); location.reload(); }}>
                  Change PC address ({serverBase().replace(/^https?:\/\//, "")})
                </button>
              )}
            </div>
          </div>
        </div>
      )}
      <nav className="fixed bottom-0 left-0 right-0 z-40 grid grid-cols-5 border-t border-slate-200 bg-white/95 backdrop-blur dark:border-slate-800 dark:bg-slate-900/95 md:hidden"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
        {TABS.map(([to, label, icon]) => (
          <NavLink key={to} to={to} end={to === "/"} onClick={() => setNavOpen(false)}
            className={({ isActive }) => `flex flex-col items-center py-2 text-[11px] ${isActive ? "text-blue-600 dark:text-blue-400" : "muted"}`}>
            <span className="text-lg leading-none">{icon}</span>{label}
          </NavLink>
        ))}
        <button className={`flex flex-col items-center py-2 text-[11px] ${navOpen ? "text-blue-600" : "muted"}`} onClick={() => setNavOpen(!navOpen)}>
          <span className="text-lg leading-none">☰</span>More
        </button>
      </nav>
      <main className="mx-auto max-w-7xl p-3 pb-24 md:pb-3">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/chart" element={<ChartPage />} />
          <Route path="/chain" element={<ChainPage />} />
          <Route path="/greeks" element={<GreeksPage />} />
          <Route path="/signals" element={<SignalsPage />} />
          <Route path="/trades" element={<TradesPage />} />
          <Route path="/risk" element={<RiskPage />} />
          <Route path="/journal" element={<JournalPage />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/health" element={<HealthPage />} />
        </Routes>
      </main>
      <footer className="muted mx-auto max-w-7xl p-3 text-xs">
        Decision-support software. TradingView/charts are visual reference only; the Python engine with Dhan data is the decision authority.
        Nothing here is investment advice or evidence of profitability.
      </footer>
    </div>
  );
}
