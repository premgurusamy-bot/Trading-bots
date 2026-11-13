import { useState } from "react";
import { api } from "../lib/api";
import { Badge, Card, ConfirmButton, ErrorBox, JsonBlock, Kv, Loading, stateTone, usePoll } from "../components/ui";
import { dt, inr, num, pnlClass } from "../lib/format";

function PendingLive() {
  const { data, reload } = usePoll<any[]>("/api/live/pending", 2000);
  if (!data || data.length === 0) return null;
  return (
    <Card title={<span className="text-red-600">LIVE orders awaiting your confirmation</span>}>
      {data.map((p) => {
        const s = p.summary;
        return (
          <div key={p.trade_id} className="mb-3 rounded border border-red-400 p-2 text-sm">
            <div className="grid grid-cols-2 gap-x-4 sm:grid-cols-3">
              <Kv k="Instrument" v={`${s.index} ${s.trading_symbol}`} /><Kv k="Type" v={s.option_type} /><Kv k="Expiry" v={s.expiry} />
              <Kv k="Strike" v={s.strike} /><Kv k="Lots × size" v={`${s.lots} × ${s.lot_size} = ${s.qty}`} /><Kv k="Premium (ask+slip)" v={num(s.premium)} />
              <Kv k="Capital required" v={inr(s.capital_required)} /><Kv k="Est. charges" v={inr(s.est_charges, 2)} /><Kv k="Maximum risk" v={inr(s.max_risk)} />
              <Kv k="Premium SL" v={num(s.premium_sl)} /><Kv k="Underlying SL" v={num(s.underlying_sl)} />
              <Kv k="Targets" v={`${num(s.targets.t1)} / ${num(s.targets.t2)} / ${s.targets.t3 ? num(s.targets.t3) : "n/a"}`} />
            </div>
            <p className="muted mt-1 text-xs">{s.exit_control}</p>
            <div className="mt-2"><ConfirmButton danger label="Send LIVE order" prompt="All checks will be re-run with fresh data before sending. Send this REAL order to Dhan?"
              onConfirm={async () => { await api(`/api/live/confirm/${p.trade_id}`, { method: "POST", body: { confirm: true } }); reload(); }} /></div>
          </div>
        );
      })}
    </Card>
  );
}

function Detail({ id }: { id: string }) {
  const { data, error } = usePoll<any>(`/api/trades/${id}`, 3000, [id]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const t = data.trade;
  return (
    <div className="mt-2 grid gap-3 lg:grid-cols-2">
      <div>
        <Kv k="Contract" v={t.option?.trading_symbol} /><Kv k="Lots / qty" v={`${t.lots} / ${t.qty} (open ${t.open_qty})`} />
        <Kv k="Entry avg / exit avg" v={`${num(t.entry_avg)} / ${num(t.exit_avg)}`} />
        <Kv k="Gross / charges / net" v={<span>{inr(t.gross_pnl, 2)} / {inr(t.charges, 2)} / <b className={pnlClass(t.net_pnl)}>{inr(t.net_pnl, 2)}</b></span>} />
        <Kv k="Entry slippage vs expected" v={inr(t.slippage, 2)} />
        <Kv k="Premium high / low after entry" v={`${num(t.premium_high)} / ${num(t.premium_low)}`} />
        <Kv k="Stop (now) / premium SL" v={`${num(t.exit_plan?.stop)} / ${num(t.exit_plan?.premium_sl)}`} />
        <Kv k="Exit reason" v={t.exit_reason || "—"} />
        {t.journal?.outcome && <p className="mt-2 text-xs"><b>Outcome (from recorded prices):</b> {t.journal.outcome.explanation}</p>}
        {data.secure_exit && <Kv k="Secure-exit floor / peak" v={`${inr(data.secure_exit.floor)} / ${inr(data.secure_exit.peak_gross)}`} />}
      </div>
      <div>
        <div className="mb-1 text-xs font-semibold">Orders (broker-confirmed fills)</div>
        <table className="tbl"><thead><tr><th>Purpose</th><th>Side</th><th>Qty</th><th>Filled</th><th>Avg</th><th>Status</th></tr></thead>
          <tbody>{data.orders.map((o: any) => <tr key={o.client_order_id}><td>{o.purpose}</td><td>{o.side}</td><td>{o.qty}</td><td>{o.filled_qty}</td><td>{num(o.avg_price)}</td><td><Badge tone={stateTone(o.status)}>{o.status}</Badge> {o.reason && <span className="muted">{o.reason}</span>}</td></tr>)}</tbody></table>
        <details className="mt-2"><summary className="cursor-pointer text-xs">Event log</summary><JsonBlock value={t.journal?.events} /></details>
      </div>
    </div>
  );
}

export default function TradesPage() {
  const { data, error } = usePoll<any[]>("/api/trades?limit=100", 3000);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="space-y-3">
      <PendingLive />
      <Card title="Positions & trades" right={<a className="btn-ghost" href="/api/export/journal.csv" onClick={(e) => { e.preventDefault(); api("/api/export/journal.csv").then((txt) => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([txt as any], { type: "text/csv" })); a.download = "journal.csv"; a.click(); }); }}>Export CSV</a>}>
        <ErrorBox error={error} />
        {!data ? <Loading /> : data.length === 0 ? <div className="muted text-sm">No trades yet.</div> : data.map((t) => (
          <div key={t.id} className="border-b border-slate-100 py-2 dark:border-slate-800">
            <button className="flex w-full flex-wrap items-center gap-2 text-left text-sm" onClick={() => setOpen(open === t.id ? null : t.id)}>
              <Badge tone={t.mode === "LIVE" ? "red" : "blue"}>{t.mode}</Badge><Badge tone={stateTone(t.state)}>{t.state}</Badge>
              <b>{t.index}</b> {t.option?.trading_symbol || ""} <span className="muted">setup {t.setup}</span>
              <span className={`tabular-nums ${pnlClass(t.net_pnl)}`}>{t.state === "CLOSED" ? `net ${inr(t.net_pnl, 2)}` : `gross ${inr(t.gross_pnl, 2)}`}</span>
              <span className="muted text-xs">{dt(t.opened_at)}</span>
            </button>
            {open === t.id && <Detail id={t.id} />}
          </div>
        ))}
      </Card>
    </div>
  );
}
