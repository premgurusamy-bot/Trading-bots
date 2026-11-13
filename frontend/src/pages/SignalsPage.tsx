import { useState } from "react";
import { Badge, Card, ErrorBox, Kv, Loading, Mandatory, ScoreBreakdown, stateTone, usePoll } from "../components/ui";
import { num, time } from "../lib/format";

export default function SignalsPage() {
  const [day, setDay] = useState(new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" }));
  const { data, error } = usePoll<any[]>(`/api/signals?day=${day}`, 3000, [day]);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="space-y-3">
      <Card title="Signals (pending → triggered → executed / cancelled / expired)" right={<input type="date" className="input w-auto" value={day} onChange={(e) => setDay(e.target.value)} />}>
        <ErrorBox error={error} />
        {!data ? <Loading /> : data.length === 0 ? <div className="muted text-sm">No signals for this day. Rejected candidates are in Journal → Research log.</div> : (
          <div className="space-y-2">
            {data.map((s) => (
              <div key={s.id} className="rounded-lg border border-slate-200 p-2 dark:border-slate-800">
                <button className="flex w-full flex-wrap items-center gap-2 text-left" onClick={() => setOpen(open === s.id ? null : s.id)}>
                  <Badge tone={stateTone(s.state)}>{s.state}</Badge>
                  <b>{s.index}</b>
                  <Badge tone={s.direction === "BULLISH" ? "green" : "red"}>{s.direction === "BULLISH" ? "CE" : "PE"} · setup {s.setup}</Badge>
                  <span className="tabular-nums">score {s.score.total}</span>
                  <span className="muted text-xs">confirm {time(s.confirm_ts)} · expires {time(s.expires_at)}</span>
                </button>
                {open === s.id && (
                  <div className="mt-2 grid gap-3 lg:grid-cols-2">
                    <div>
                      <Kv k="Entry trigger" v={num(s.plan.trigger)} />
                      <Kv k="Underlying SL" v={num(s.plan.stop)} />
                      <Kv k="Risk (pts)" v={num(s.plan.risk_points)} />
                      <Kv k="T1 / T2 / T3" v={`${num(s.plan.t1)} / ${num(s.plan.t2)} / ${s.plan.t3 ? num(s.plan.t3) : "n/a"}`} />
                      <Kv k="Next S/R" v={s.plan.next_sr ? `${s.plan.next_sr.source} ${num(s.plan.next_sr.price)} (RR ${s.plan.rr_to_next_sr})` : "none found"} />
                      <Kv k="Contract" v={s.option ? `${s.option.strike} ${s.option.option_type} (Δ ${num(s.option.delta, 2)})` : "—"} />
                      <Kv k="Premium entry est / SL" v={s.premium ? `${num(s.premium.entry_premium_est)} / ${num(s.premium.premium_sl)}` : "—"} />
                      <p className="muted mt-1 text-xs">{s.premium?.controls_exit}</p>
                      <div className="mt-2 text-xs font-semibold">Invalidation</div>
                      <ul className="muted list-disc pl-4 text-xs">{s.invalidation.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
                      <div className="mt-2 text-xs font-semibold">History</div>
                      <ul className="text-xs">{s.history.map((h: any, i: number) => <li key={i}>{time(h.at)} {h.from || "∅"} → <b>{h.to}</b>: {h.reason}</li>)}</ul>
                      <div className="muted mt-1 text-xs">Strategy {s.strategy_version} · params {s.params_hash} · snapshot {s.snapshot_ref}</div>
                    </div>
                    <div className="space-y-2"><Mandatory m={s.mandatory} /><ScoreBreakdown score={s.score} /></div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
