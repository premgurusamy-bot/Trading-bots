import { useState } from "react";
import { api } from "../lib/api";
import { Badge, Card, ConfirmButton, ErrorBox, Kv, Loading, usePoll } from "../components/ui";
import { dt, inr } from "../lib/format";

function Credentials() {
  const st = usePoll<any>("/api/credentials", 15000);
  const [cid, setCid] = useState("");
  const [tok, setTok] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const save = async () => {
    setErr(null); setMsg(null);
    try {
      const r = await api("/api/credentials", { method: "POST", body: { client_id: cid, access_token: tok } });
      setTok(""); setMsg(`Saved to ${r.status.store}. Feed: ${r.feed}`); st.reload();
    } catch (e) { setErr(e); }
  };
  const s = st.data;
  return (
    <Card title="Dhan credentials (write-only)">
      <p className="muted mb-2 text-xs">Stored in the Windows Credential Manager on this PC. They are never shown again, never sent to the browser and never logged. Generate a fresh access token on web.dhan.co → My Profile → DhanHQ Trading APIs when it expires.</p>
      {s && (
        <div className="mb-2">
          <Kv k="Client ID" v={s.client_id || "not set"} /><Kv k="Access token" v={s.access_token ? "set" : "not set"} />
          <Kv k="Token expires" v={s.token_expires_at ? <span>{dt(s.token_expires_at)} {s.token_expired && <Badge tone="red">EXPIRED</Badge>}</span> : "unknown"} />
          <Kv k="Storage" v={s.store} />
        </div>
      )}
      <div className="space-y-2">
        <input className="input" placeholder="Dhan client ID" value={cid} onChange={(e) => setCid(e.target.value)} autoComplete="off" />
        <input className="input" placeholder="Access token" type="password" value={tok} onChange={(e) => setTok(e.target.value)} autoComplete="off" />
        <div className="flex flex-wrap gap-2">
          <button className="btn-primary" disabled={!cid || tok.length < 20} onClick={save}>Save & reconnect</button>
          <button className="btn-ghost" onClick={async () => { await api("/api/feed/restart", { method: "POST" }); st.reload(); }}>Reconnect feed</button>
          <ConfirmButton danger label="Delete credentials" prompt="Remove stored Dhan credentials from this PC?" onConfirm={async () => { await api("/api/credentials", { method: "DELETE" }); st.reload(); }} />
        </div>
        {msg && <div className="text-sm text-green-600">{msg}</div>}
        <ErrorBox error={err} />
      </div>
    </Card>
  );
}

function ParamTable({ s, reload }: { s: any; reload: () => void }) {
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [err, setErr] = useState<unknown>(null);
  const spec = new Set(s.spec_fields);
  const keys = Object.keys(s.params);
  const save = async (specText?: string) => {
    setErr(null);
    const changes: Record<string, any> = {};
    for (const [k, v] of Object.entries(edits)) {
      const cur = s.params[k];
      changes[k] = typeof cur === "number" ? Number(v) : Array.isArray(cur) ? v.split(",").map(Number) : v;
    }
    await api("/api/settings/strategy", { method: "PUT", body: { changes, confirm: true, spec_confirm_text: specText } });
    setEdits({}); reload();
  };
  const touchesSpec = Object.keys(edits).some((k) => spec.has(k));
  return (
    <Card title={<span>Strategy parameters <Badge tone="blue">{s.strategy_version}</Badge> {Object.keys(s.defaults_changed).length ? <Badge tone="amber">defaults changed: {Object.keys(s.defaults_changed).length}</Badge> : <Badge tone="green">frozen defaults</Badge>}</span>}>
      <p className="muted mb-2 text-xs"><Badge tone="blue">SPEC</Badge> values are frozen V1.0 values; changing them needs extra confirmation. Other values are implementer defaults where V1.0 gave no number (see docs/STRATEGY_V1.md). Changes are audited.</p>
      <div className="max-h-[480px] overflow-auto">
        <table className="tbl">
          <thead><tr><th>Parameter</th><th>Current</th><th>Default</th><th>New value</th></tr></thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k} className={s.defaults_changed[k] ? "bg-amber-50 dark:bg-amber-950" : ""}>
                <td>{k} {spec.has(k) && <Badge tone="blue">SPEC</Badge>}</td>
                <td className="tabular-nums">{String(s.params[k])}</td>
                <td className="muted tabular-nums">{String(s.defaults[k])}</td>
                <td><input className="input py-1" style={{ minHeight: 30 }} value={edits[k] ?? ""} placeholder="—" onChange={(e) => setEdits({ ...edits, [k]: e.target.value })} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ErrorBox error={err} />
      <div className="mt-2 flex flex-wrap gap-2">
        {Object.keys(edits).length > 0 && (touchesSpec
          ? <ConfirmButton danger label="Save (changes frozen values)" requireText="CHANGE FROZEN V1.0 VALUES" prompt="You are changing frozen Strategy V1.0 values. This changes strategy behaviour." onConfirm={(t) => save(t)} />
          : <ConfirmButton label="Save changes" prompt="Apply these strategy setting changes? (audited)" onConfirm={() => save()} />)}
        <ConfirmButton label="Restore V1.0 defaults" prompt="Reset all strategy parameters to frozen V1.0 defaults?" onConfirm={async () => { await api("/api/settings/strategy/reset-defaults", { method: "POST", body: { confirm: true } }); reload(); }} />
      </div>
    </Card>
  );
}

function SecureExit({ s, reload }: { s: any; reload: () => void }) {
  const [vals, setVals] = useState<Record<string, string>>({});
  const cur = s.secure_exit;
  return (
    <Card title="Secure-Exit settings (risk limits — explicit control only)">
      {Object.keys(cur).map((k) => (
        <div key={k} className="flex items-center justify-between gap-2 py-0.5 text-sm">
          <span className="muted">{k}</span>
          <span className="flex items-center gap-2"><span className="tabular-nums">{String(cur[k])}</span>
            <input className="input w-28 py-1" style={{ minHeight: 30 }} placeholder="new" value={vals[k] ?? ""} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} /></span>
        </div>
      ))}
      {Object.keys(vals).length > 0 && <ConfirmButton danger label="Save risk settings" prompt="Change Secure-Exit / loss-limit settings? This changes your risk limits (audited)."
        onConfirm={async () => {
          const values: Record<string, any> = {};
          for (const [k, v] of Object.entries(vals)) values[k] = typeof cur[k] === "number" ? Number(v) : typeof cur[k] === "boolean" ? v === "true" : v;
          await api("/api/settings/secure-exit", { method: "PUT", body: { values, confirm: true } }); setVals({}); reload();
        }} />}
    </Card>
  );
}

function Mode({ s }: { s: any }) {
  const h = usePoll<any>("/api/health", 5000);
  const [ack, setAck] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const ACK = "I UNDERSTAND LIVE TRADING PLACES REAL ORDERS WITH REAL MONEY";
  const live = h.data?.mode === "LIVE";
  return (
    <Card title="Trading mode">
      <Kv k="Current mode" v={<Badge tone={live ? "red" : "blue"}>{h.data?.mode || "—"}</Badge>} />
      <Kv k="Live feature flag (.env)" v={s.live_feature_flag ? <Badge tone="amber">true</Badge> : <Badge tone="green">false (live impossible)</Badge>} />
      <p className="muted my-2 text-xs">Live trading needs: the .env feature flag, a connected Dhan feed, your password, this acknowledgement, and then your confirmation of a pre-trade summary for EVERY order. Mode returns to PAPER on every restart.</p>
      {!live ? (
        <div className="space-y-2">
          <input className="input" placeholder={ACK} value={ack} onChange={(e) => setAck(e.target.value)} />
          <input className="input" type="password" placeholder="Dashboard password" value={pw} onChange={(e) => setPw(e.target.value)} />
          <ConfirmButton danger label="Enable LIVE trading" prompt="Enable live trading with real money?" onConfirm={async () => {
            setErr(null);
            try { const r = await api("/api/mode/live", { method: "POST", body: { acknowledgement: ack, password: pw } }); setMsg(r.message); h.reload(); } catch (e) { setErr(e); throw e; }
          }} />
        </div>
      ) : (
        <button className="btn-primary" onClick={async () => { try { await api("/api/mode/paper", { method: "POST" }); h.reload(); } catch (e) { setErr(e); } }}>Return to PAPER mode</button>
      )}
      {msg && <div className="mt-2 text-sm">{msg}</div>}
      <ErrorBox error={err} />
    </Card>
  );
}

function Paper({ s, reload }: { s: any; reload: () => void }) {
  const [cap, setCap] = useState(String(s.paper_capital));
  return (
    <Card title="Paper account">
      <Kv k="Paper capital" v={inr(s.paper_capital)} />
      <label className="label mt-2">New starting capital</label>
      <input className="input mb-2" value={cap} onChange={(e) => setCap(e.target.value)} />
      <ConfirmButton danger label="Reset paper account" requireText="RESET PAPER ACCOUNT" prompt="Reset the paper balance (history is kept; the reset is audited)."
        onConfirm={async (t) => { await api("/api/paper/reset", { method: "POST", body: { text: t, capital: Number(cap) } }); reload(); }} />
    </Card>
  );
}

function Charges({ s, reload }: { s: any; reload: () => void }) {
  const [txt, setTxt] = useState(JSON.stringify(s.charges, null, 2));
  const [err, setErr] = useState<unknown>(null);
  return (
    <Card title="Charge schedule (effective-dated)">
      <p className="muted mb-2 text-xs">Default rates must be checked against current NSE/Dhan/tax notifications (docs/CHARGES.md). Add a new entry with a new effective_from date rather than editing history.</p>
      <textarea className="input h-64 font-mono text-xs" value={txt} onChange={(e) => setTxt(e.target.value)} />
      <ErrorBox error={err} />
      <ConfirmButton label="Save schedule" prompt="Save the charge schedule? (audited)" onConfirm={async () => {
        try { await api("/api/settings/charges", { method: "PUT", body: { schedule: JSON.parse(txt), confirm: true } }); reload(); } catch (e) { setErr(e); throw e; }
      }} />
    </Card>
  );
}

export default function SettingsPage() {
  const { data, error, reload } = usePoll<any>("/api/settings", 60000);
  if (error && !data) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <Credentials />
      <Mode s={data} />
      <div className="lg:col-span-2"><ParamTable s={data} reload={reload} /></div>
      <SecureExit s={data} reload={reload} />
      <Paper s={data} reload={reload} />
      <div className="lg:col-span-2"><Charges s={data} reload={reload} /></div>
      <Card title="Notifications">
        <p className="muted text-sm">In-app alerts appear for signals, trades, confirmations and system faults while the dashboard is open. Push notifications are not enabled because they can't be guaranteed reliable on all devices. Always confirm in the Dhan app.</p>
      </Card>
    </div>
  );
}
