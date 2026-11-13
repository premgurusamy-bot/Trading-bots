import { useState } from "react";
import { Badge, Card, ErrorBox, JsonBlock, Kv, Loading, stateTone, usePoll } from "../components/ui";
import { dt, inr, num } from "../lib/format";

function Stats({ s, unit = "₹" }: { s: any; unit?: string }) {
  if (!s || !s.trades) return <div className="muted text-sm">No trades.</div>;
  const v = (x: number) => (unit === "₹" ? inr(x, 2) : `${num(x, 2)}${unit}`);
  return (
    <div className="grid grid-cols-2 gap-x-4 sm:grid-cols-4">
      <Kv k="Trades" v={s.trades} /><Kv k="Win rate" v={`${s.win_rate}%`} /><Kv k="Avg win" v={v(s.avg_win)} /><Kv k="Avg loss" v={v(s.avg_loss)} />
      <Kv k="Expectancy" v={v(s.expectancy)} /><Kv k="Profit factor" v={s.profit_factor ?? "—"} /><Kv k="Max drawdown" v={v(s.max_drawdown)} /><Kv k="Net" v={v(s.net)} />
      <Kv k="Max losing streak" v={s.max_losing_streak} /><Kv k="Sample adequate" v={s.sample_adequate ? "yes" : <Badge tone="amber">no (&lt;30)</Badge>} />
    </div>
  );
}

export { Stats };

export default function JournalPage() {
  const [tab, setTab] = useState<"analytics" | "research">("analytics");
  const an = usePoll<any>("/api/analytics", 10000);
  const rs = usePoll<any[]>(tab === "research" ? "/api/research?limit=300" : null, 10000, [tab]);
  const [snap, setSnap] = useState<any>(null);
  return (
    <div className="space-y-3">
      <div className="flex gap-1">
        <button className={tab === "analytics" ? "btn-primary" : "btn-ghost"} onClick={() => setTab("analytics")}>Execution analytics</button>
        <button className={tab === "research" ? "btn-primary" : "btn-ghost"} onClick={() => setTab("research")}>Research log (rejected / expired)</button>
      </div>
      {tab === "analytics" && (
        <>
          <ErrorBox error={an.error} />
          {!an.data ? <Loading /> : (
            <>
              <Card title="Executed trades (net of charges)">
                {an.data.execution.warning && <div className="mb-2 text-xs text-amber-600">{an.data.execution.warning}</div>}
                <Stats s={an.data.execution.overall} />
                <Kv k="Total charges" v={inr(an.data.execution.costs, 2)} />
              </Card>
              <div className="grid gap-3 md:grid-cols-3">
                {(["by_index", "by_setup", "by_hour"] as const).map((k) => (
                  <Card key={k} title={k.replace("by_", "By ")}>
                    {Object.entries(an.data.execution[k] || {}).map(([g, s]: any) => <div key={g} className="text-sm"><b>{g}</b>: {s.trades} trades, win {s.win_rate}%, net {inr(s.net, 0)}</div>)}
                  </Card>
                ))}
              </div>
              <Card title="Research summary (kept separate from executions)">
                <p className="muted text-xs">{an.data.note}</p>
                <div className="mt-1 flex flex-wrap gap-1">{Object.entries(an.data.research.by_kind).map(([k, v]: any) => <Badge key={k} tone={stateTone(k)}>{k}: {v}</Badge>)}</div>
                <div className="mt-2 text-xs font-semibold">Most common failed conditions</div>
                {an.data.research.top_reasons.map(([r, n]: any) => <div key={r} className="text-xs">{n} × {r}</div>)}
              </Card>
            </>
          )}
        </>
      )}
      {tab === "research" && (
        <Card title="Rejected, cancelled and expired candidates">
          <ErrorBox error={rs.error} />
          {!rs.data ? <Loading /> : (
            <div className="overflow-x-auto">
              <table className="tbl">
                <thead><tr><th>Time</th><th>Index</th><th>Kind</th><th>Score</th><th>Reasons</th><th>Premium hi/lo after</th><th>Snapshot</th></tr></thead>
                <tbody>
                  {rs.data.map((r) => (
                    <tr key={r.id}>
                      <td className="whitespace-nowrap">{dt(r.created_at)}</td><td>{r.index}</td><td><Badge tone={stateTone(r.kind)}>{r.kind}</Badge></td><td>{r.score}</td>
                      <td className="muted">{(r.reasons || []).join(", ")}</td><td>{num(r.premium_high_after)} / {num(r.premium_low_after)}</td>
                      <td>{r.snapshot_ref && <button className="text-blue-600 underline" onClick={() => fetchSnap(r.snapshot_ref, setSnap)}>{r.snapshot_ref.slice(0, 8)}</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
      {snap && <Card title={`Decision snapshot ${snap.ref}`} right={<button className="btn-ghost" onClick={() => setSnap(null)}>Close</button>}><JsonBlock value={snap} /></Card>}
    </div>
  );
}

async function fetchSnap(ref: string, set: (x: any) => void) {
  const { api } = await import("../lib/api");
  set(await api(`/api/snapshot/${ref}`));
}
