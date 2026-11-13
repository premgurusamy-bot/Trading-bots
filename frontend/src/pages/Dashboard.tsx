import { Badge, Card, ErrorBox, Kv, Loading, Mandatory, ScoreBreakdown, stateTone, usePoll } from "../components/ui";
import { age, inr, num, pct, pnlClass, time } from "../lib/format";

function IndexCard({ name, d }: { name: string; d: any }) {
  const t = d.trend;
  const fresh = d.quality;
  return (
    <Card
      title={<span>{name === "BANKNIFTY" ? "BANK NIFTY" : name}</span>}
      right={<Badge tone={stateTone(d.state)}>{d.state || "NO DATA"}</Badge>}
    >
      <div className="flex items-baseline gap-3">
        <span className="text-3xl font-bold tabular-nums">{num(d.ltp)}</span>
        <span className={`tabular-nums ${pnlClass(d.change)}`}>
          {d.change !== null && d.change !== undefined ? `${d.change > 0 ? "+" : ""}${num(d.change)} (${pct(d.change_pct)})` : "—"}
        </span>
      </div>
      <div className="muted mb-2 text-xs">
        tick age {age(d.tick_age_s)} · chain age {age(d.chain_age_s)} · last 5m close {time(d.last_candle)} · evaluated {time(d.evaluated_at)}
      </div>
      {fresh && !fresh.ok && (
        <div className="mb-2 rounded border border-amber-400 bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          <b>New entries blocked — data fault:</b> {fresh.reasons.join("; ")}
        </div>
      )}
      <div className="grid grid-cols-2 gap-x-4 sm:grid-cols-4">
        <Kv k="15m trend" v={t ? (t.bullish.ema15_9_above_20 ? "Bullish" : t.bearish.ema15_9_below_20 ? "Bearish" : "Flat") : "—"} />
        <Kv k="5m trend" v={t ? (t.bullish.ema5_9_above_20 ? "Bullish" : t.bearish.ema5_9_below_20 ? "Bearish" : "Flat") : "—"} />
        <Kv k="VWAP" v={t?.values?.vwap ? (t.bullish.price_above_vwap ? "Above" : "Below") + ` ${num(t.values.vwap)}` : "—"} />
        <Kv k="ADX" v={num(t?.values?.adx, 1)} />
        <Kv k="Entries today" v={`${d.entries_today} / ${d.max_entries}`} />
        <Kv k="Active position" v={d.active_trade ? <Badge tone="green">yes</Badge> : "none"} />
        <Kv k="Sideways" v={t ? (t.sideways ? "yes" : "no") : "—"} />
        <Kv k="Risk gate" v={d.risk_gate ? (d.risk_gate.ok ? "open" : "blocked") : "—"} />
      </div>
      {d.risk_gate && !d.risk_gate.ok && <div className="muted mt-1 text-xs">{d.risk_gate.reasons.join("; ")}</div>}
      <div className="mt-2 space-y-1 text-xs">
        {(d.messages || []).map((m: string, i: number) => <div key={i} className="muted">• {m}</div>)}
      </div>
      {d.candidate && (
        <div className="mt-3 space-y-2">
          <div className="text-sm font-semibold">Candidate: setup {d.candidate.setup || "—"} {d.candidate.direction}</div>
          <Mandatory m={d.candidate.mandatory} />
          <ScoreBreakdown score={d.score} />
        </div>
      )}
    </Card>
  );
}

export default function Dashboard() {
  const { data, error } = usePoll<any>("/api/dashboard", 2000);
  if (error && !data) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-3">
        <Card title="Session & connection">
          <Kv k="Market session" v={data.session} />
          <Kv k="Mode" v={<Badge tone={data.mode === "LIVE" ? "red" : "blue"}>{data.mode}</Badge>} />
          <Kv k="Feed" v={<Badge tone={data.feed.connected ? "green" : "red"}>{data.feed.connected ? "connected" : "disconnected"}</Badge>} />
          <Kv k="Source" v={data.feed.source} />
          <Kv k="Token" v={data.feed.token_status} />
          <Kv k="Updated" v={time(data.now)} />
          {data.feed.faults.length > 0 && <div className="mt-1 text-xs text-red-600 dark:text-red-400">{data.feed.faults.join("; ")}</div>}
        </Card>
        <Card title="P&L today">
          <Kv k="Realised gross" v={<span className={pnlClass(data.pnl.realised_gross)}>{inr(data.pnl.realised_gross, 2)}</span>} />
          <Kv k="Realised net (after charges)" v={<span className={pnlClass(data.pnl.realised_net)}>{inr(data.pnl.realised_net, 2)}</span>} />
          <Kv k="Unrealised gross (at bid)" v={<span className={pnlClass(data.pnl.unrealised_gross)}>{inr(data.pnl.unrealised_gross, 2)}</span>} />
          <Kv k="Available balance" v={inr(data.pnl.available_balance)} />
          <Kv k="Capital in use" v={inr(data.pnl.used_capital)} />
        </Card>
        <Card title="Risk status">
          <Kv k="Daily loss limit" v={inr(data.risk.daily_loss_limit)} />
          <Kv k="New entries" v={data.risk.entries_enabled ? "enabled" : <Badge tone="amber">disabled</Badge>} />
          <Kv k="Kill switch" v={data.risk.kill_switch ? <Badge tone="red">ENGAGED</Badge> : "off"} />
          <Kv k="Order state" v={data.risk.order_state_uncertain ? <Badge tone="red">UNCERTAIN — reconcile</Badge> : "ok"} />
          <Kv k="Blocked" v={data.risk.blocked_reason || "no"} />
        </Card>
      </div>
      <div className="grid min-w-0 gap-3 lg:grid-cols-2">
        {Object.entries(data.indices).map(([k, v]) => <IndexCard key={k} name={k} d={v} />)}
      </div>
    </div>
  );
}
