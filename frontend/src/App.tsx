import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, connectEvents, getToken, setToken, setUnauthorizedHandler, WsEvent } from "./lib/api";
import { Badge, ErrorBox } from "./components/ui";
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
  if (!state) return <div className="p-6"><ErrorBox error={err} /></div>;
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
      </div>
    </div>
  );
}

export default function App() {
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
      }
    }, setWsState);
    const id = window.setInterval(() => api("/api/health").then((h) => setMode(h.mode)).catch(() => {}), 5000);
    api("/api/health").then((h) => setMode(h.mode)).catch(() => {});
    return () => {
      stop();
      window.clearInterval(id);
    };
  }, [authed]);

  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  return (
    <div className="min-h-screen">
      <header className={`sticky top-0 z-40 border-b ${mode === "LIVE" ? "border-red-700 bg-red-700 text-white" : "border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-900/90"}`}>
        <div className="mx-auto flex max-w-7xl items-center gap-2 px-3 py-2">
          <button className="btn-ghost px-2 md:hidden" aria-label="Menu" onClick={() => setNavOpen(!navOpen)}>☰</button>
          <span className="font-bold">Options Desk</span>
          <Badge tone={mode === "LIVE" ? "red" : "blue"}>{mode === "LIVE" ? "LIVE — REAL MONEY" : "PAPER"}</Badge>
          <Badge tone={wsState === "open" ? "green" : "amber"}>{wsState === "open" ? "events ●" : "events ○"}</Badge>
          <div className="flex-1" />
          <button className="btn-ghost px-2 text-xs" onClick={() => { api("/api/auth/logout", { method: "POST" }).catch(() => {}); setToken(null); setAuthed(false); }}>Log out</button>
        </div>
        <nav className={`${navOpen ? "block" : "hidden"} mx-auto max-w-7xl px-2 pb-2 md:block`}>
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
        <div className="fixed bottom-4 right-4 z-50 max-w-sm rounded-lg bg-slate-900 p-3 text-sm text-white shadow-lg">
          <b>{toast.kind}</b>: {toast.message || toast.state || toast.id || toast.trade_id || ""}
        </div>
      )}
      <main className="mx-auto max-w-7xl p-3">
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
