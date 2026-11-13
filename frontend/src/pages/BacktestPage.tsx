import { useState } from "react";
import { api } from "../lib/api";
import { Badge, Card, ErrorBox, JsonBlock, Kv, usePoll } from "../components/ui";
import { dt } from "../lib/format";
import { Stats } from "./JournalPage";

export default function BacktestPage() {
  const runs = usePoll<any[]>("/api/backtest/runs", 30000);
  const [index, setIndex] = useState("NIFTY");
  const today = new Date();
  const [from, setFrom] = useState(new Date(today.getTime() - 60 * 86400000).toISOString().slice(0, 10));
  const [to, setTo] = useState(today.toISOString().slice(0, 10));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [sel, setSel] = useState<any>(null);
  const run = async () => {
    setBusy(true); setErr(null);
    try { setSel(await api("/api/backtest/run", { method: "POST", body: { index, from_date: from, to_date: to } })); runs.reload(); }
    catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const r = sel?.results;
  const unit = r?.unit?.startsWith("R") ? "R" : "₹";
  return (
    <div className="space-y-3">
      <Card title="Run a backtest on genuine Dhan history">
        <p className="muted mb-2 text-xs">Uses genuine underlying 5-minute candles (and ATM option premiums where Dhan provides them). Historical option-chain OI/PCR/Max Pain/Greeks are NOT available, so results are labelled <b>technical-only</b> and are not a full-strategy backtest. Periods are split 60/20/20 into development / validation / out-of-sample; no parameters are optimised.</p>
        <div className="flex flex-wrap items-end gap-2">
          <div><label className="label">Index</label><select className="input" value={index} onChange={(e) => setIndex(e.target.value)}><option>NIFTY</option><option>BANKNIFTY</option></select></div>
          <div><label className="label">From</label><input type="date" className="input" value={from} onChange={(e) => setFrom(e.target.value)} /></div>
          <div><label className="label">To</label><input type="date" className="input" value={to} onChange={(e) => setTo(e.target.value)} /></div>
          <button className="btn-primary" disabled={busy} onClick={run}>{busy ? "Running…" : "Run"}</button>
        </div>
        <ErrorBox error={err} />
      </Card>
      {r && (
        <Card title={<span>Result <Badge tone="amber">{r.kind}</Badge></span>}>
          <div className="mb-2 rounded bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">{r.disclaimer}</div>
          <Kv k="Unit" v={r.unit} /><Kv k="Coverage" v={`${r.data_coverage.days} days (${r.data_coverage.first} → ${r.data_coverage.last}), ${r.data_coverage.bars} bars`} />
          <Kv k="Volume coverage" v={sel.provenance.volume_coverage} /><Kv k="Technical candidates" v={r.technical_candidates} />
          {r.overall.warning && <div className="text-xs text-amber-600">{r.overall.warning}</div>}
          <div className="mt-2"><Stats s={r.overall.overall} unit={unit} /></div>
          {Object.entries(r.periods).map(([k, p]: any) => <div key={k} className="mt-3"><div className="text-sm font-semibold">{k} ({p.from} → {p.to})</div><Stats s={p.metrics.overall} unit={unit} /></div>)}
          <details className="mt-2"><summary className="cursor-pointer text-xs">Provenance, config & trades</summary><JsonBlock value={{ provenance: sel.provenance, config: sel.config, rejected: r.rejected_reasons, trades: r.trades }} /></details>
        </Card>
      )}
      <Card title="Previous runs">
        <ErrorBox error={runs.error} />
        {(runs.data || []).map((x) => <button key={x.id} className="block w-full border-b border-slate-100 py-1 text-left text-sm dark:border-slate-800" onClick={() => setSel(x)}>{dt(x.created_at)} · {x.config.index} · <Badge>{x.kind}</Badge> · {x.results.overall.overall.trades ?? 0} trades</button>)}
      </Card>
    </div>
  );
}
