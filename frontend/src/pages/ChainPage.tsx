import { useState } from "react";
import { Badge, Card, ErrorBox, Kv, Loading, usePoll } from "../components/ui";
import { age, num, time } from "../lib/format";

const k = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v >= 1e5 ? `${(v / 1e5).toFixed(2)}L` : v.toLocaleString("en-IN"));

export default function ChainPage() {
  const [index, setIndex] = useState("NIFTY");
  const [win, setWin] = useState(10);
  const { data, error } = usePoll<any>(`/api/chain/${index}?window=${win}`, 3000, [index, win]);
  return (
    <div className="space-y-3">
      <Card
        title="Option chain"
        right={
          <div className="flex flex-wrap gap-1">
            {["NIFTY", "BANKNIFTY"].map((i) => <button key={i} className={index === i ? "btn-primary" : "btn-ghost"} onClick={() => setIndex(i)}>{i}</button>)}
            <select className="input w-auto" value={win} onChange={(e) => setWin(+e.target.value)}>
              {[5, 10, 15, 20].map((w) => <option key={w} value={w}>±{w} strikes</option>)}
            </select>
          </div>
        }
      >
        <ErrorBox error={error} />
        {!data ? <Loading /> : !data.available ? <div className="muted p-4 text-sm">{data.reason}</div> : (
          <>
            {data.stale && <div className="mb-2 rounded bg-amber-100 p-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">STALE: chain is {age(data.age_s)} old — not used for new entries.</div>}
            <div className="mb-2 grid grid-cols-2 gap-x-4 text-sm sm:grid-cols-4">
              <Kv k="Underlying" v={num(data.underlying)} />
              <Kv k="Expiry" v={data.expiry} />
              <Kv k="Received" v={`${time(data.received_at)} (${age(data.age_s)})`} />
              <Kv k="Source" v={data.source} />
              <Kv k="PCR overall" v={num(data.analysis.pcr_overall, 3)} />
              <Kv k="PCR near-ATM" v={num(data.analysis.pcr_near_atm, 3)} />
              <Kv k="PCR change" v={`${num(data.analysis.pcr_change, 3)} (${data.analysis.pcr_basis})`} />
              <Kv k="Max Pain" v={num(data.analysis.max_pain, 0)} />
            </div>
            <div className="overflow-x-auto">
              <table className="tbl tabular-nums">
                <thead>
                  <tr>
                    <th>CE OI</th><th>ΔOI</th><th>Vol</th><th>IV</th><th>Bid</th><th>Ask</th><th>LTP</th>
                    <th className="text-center">Strike</th>
                    <th>LTP</th><th>Bid</th><th>Ask</th><th>IV</th><th>Vol</th><th>ΔOI</th><th>PE OI</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r: any) => {
                    const ce = r.ce || {}, pe = r.pe || {};
                    return (
                      <tr key={r.strike} className={r.atm ? "bg-blue-50 font-semibold dark:bg-blue-950" : ""}>
                        <td>{k(ce.oi)}</td><td className={ce.oi_change > 0 ? "text-green-600" : "text-red-600"}>{k(ce.oi !== null && ce.prev_oi !== null ? ce.oi - ce.prev_oi : null)}</td>
                        <td>{k(ce.volume)}</td><td>{num(ce.iv, 1)}</td><td>{num(ce.bid)}</td><td>{num(ce.ask)}</td><td>{num(ce.ltp)}</td>
                        <td className="text-center">{r.strike}{r.atm && <Badge tone="blue">ATM</Badge>}</td>
                        <td>{num(pe.ltp)}</td><td>{num(pe.bid)}</td><td>{num(pe.ask)}</td><td>{num(pe.iv, 1)}</td><td>{k(pe.volume)}</td>
                        <td className={(pe.oi ?? 0) - (pe.prev_oi ?? 0) > 0 ? "text-green-600" : "text-red-600"}>{k(pe.oi !== null && pe.prev_oi !== null ? pe.oi - pe.prev_oi : null)}</td><td>{k(pe.oi)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>
      {data?.available && (
        <div className="grid gap-3 md:grid-cols-2">
          <Card title="OI support / resistance (highest OI)">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div><div className="muted mb-1">Resistance (CE)</div>{data.analysis.walls.resistance.map((w: any) => <div key={w.strike}>{w.strike} — {k(w.oi)}</div>)}</div>
              <div><div className="muted mb-1">Support (PE)</div>{data.analysis.walls.support.map((w: any) => <div key={w.strike}>{w.strike} — {k(w.oi)}</div>)}</div>
            </div>
          </Card>
          <Card title="Derivatives evidence (INFERRED)">
            <p className="muted mb-2 text-xs">{data.analysis.notes.join(" ")}</p>
            {data.analysis.evidence.map((e: any) => (
              <div key={e.name} className="flex items-center justify-between py-0.5 text-sm">
                <span>{e.name.replace(/_/g, " ")} <span className="muted">({e.direction.toLowerCase()})</span></span>
                <Badge tone={e.status === "present" ? (e.direction === "BULLISH" ? "green" : "red") : e.status === "unavailable" ? "amber" : "gray"}>{e.status}</Badge>
              </div>
            ))}
          </Card>
        </div>
      )}
    </div>
  );
}
