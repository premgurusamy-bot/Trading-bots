import { Badge, Card, ErrorBox, Kv, Loading, usePoll } from "../components/ui";
import { dt, time } from "../lib/format";

export default function HealthPage() {
  const { data, error } = usePoll<any>("/api/system", 5000);
  const audit = usePoll<any[]>("/api/audit?limit=50", 30000);
  if (error && !data) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const f = data.feed;
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <Card title="Application">
        <Kv k="App version" v={data.app_version} /><Kv k="Strategy" v={data.strategy_version} />
        <Kv k="Started" v={dt(data.started_at)} /><Kv k="Engine loop" v={time(data.loop_at)} />
        <Kv k="Loop error" v={data.loop_error ? <span className="text-red-600">{data.loop_error}</span> : "none"} />
        <Kv k="Database" v={<Badge tone={data.db.ok ? "green" : "red"}>{data.db.ok ? "ok" : "error"}</Badge>} />
        <Kv k="Instrument master" v={data.instrument_master ? `v${data.instrument_master.version} · ${data.instrument_master.rows} rows · ${dt(data.instrument_master.fetched_at)}` : "not loaded"} />
      </Card>
      <Card title="Dhan connectivity">
        <Kv k="WebSocket" v={<Badge tone={f.connected ? "green" : "red"}>{f.connected ? "connected" : "disconnected"}</Badge>} />
        <Kv k="Token status" v={<Badge tone={f.token_status === "ok" ? "green" : f.token_status === "invalid" ? "red" : "amber"}>{f.token_status}</Badge>} />
        <Kv k="Token expires" v={data.credentials.token_expires_at ? dt(data.credentials.token_expires_at) : "unknown"} />
        <Kv k="Last API success" v={dt(data.dhan_api.last_ok_at)} /><Kv k="Last API error" v={data.dhan_api.last_error || "none"} />
        <Kv k="Rate limited until" v={f.rate_limited_until ? dt(f.rate_limited_until) : "no"} />
        {["NIFTY", "BANKNIFTY"].map((i) => <Kv key={i} k={`${i} tick / chain / candles`} v={`${time(f.last_tick_at[i])} / ${time(f.last_chain_at[i])} / ${time(f.last_candle_at[i])}`} />)}
        {f.faults.length > 0 && <div className="mt-2 text-sm text-red-600">Faults: {f.faults.join("; ")}</div>}
      </Card>
      <Card title="Recent feed/API errors">
        {f.errors.length === 0 ? <div className="muted text-sm">none</div> : f.errors.map((e: any, i: number) => <div key={i} className="text-xs">{time(e.at)} [{e.source}] {e.message}</div>)}
      </Card>
      <Card title="System events">
        <div className="max-h-80 overflow-auto">{data.events.map((e: any, i: number) => <div key={i} className="text-xs"><Badge tone={e.level === "ERROR" ? "red" : e.level === "WARN" ? "amber" : "gray"}>{e.level}</Badge> {dt(e.ts)} [{e.source}] {e.message}</div>)}</div>
      </Card>
      <Card title="Audit log (settings, mode, credentials)" className="lg:col-span-2">
        <div className="max-h-80 overflow-auto">{(audit.data || []).map((a, i) => <div key={i} className="text-xs">{dt(a.ts)} · <b>{a.action}</b> · {a.actor}</div>)}</div>
      </Card>
    </div>
  );
}
