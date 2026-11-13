import { useState } from "react";
import { Badge, Card, ErrorBox, Loading, usePoll } from "../components/ui";
import { num, time } from "../lib/format";

function Side({ title, sel }: { title: string; sel: any }) {
  return (
    <Card title={title} right={sel.selected ? <Badge tone="green">selected {sel.selected.moneyness}</Badge> : <Badge tone="red">none qualifies</Badge>}>
      <p className="muted mb-2 text-xs">{sel.rationale}</p>
      <div className="overflow-x-auto">
        <table className="tbl tabular-nums">
          <thead><tr><th>Contract</th><th>Δ</th><th>Γ</th><th>Θ/day</th><th>Vega</th><th>IV</th><th>LTP</th><th>Bid/Ask</th><th>Spread%</th><th>Vol</th><th>Lot</th><th>Greeks</th></tr></thead>
          <tbody>
            {sel.candidates.map((c: any) => (
              <tr key={c.strike + c.moneyness}>
                <td>{c.trading_symbol || `${c.strike} ${c.option_type}`} <Badge>{c.moneyness}</Badge></td>
                <td>{num(c.quote.delta, 3)}</td><td>{num(c.quote.gamma, 5)}</td><td>{num(c.quote.theta, 2)}</td><td>{num(c.quote.vega, 2)}</td>
                <td>{num(c.quote.iv, 2)}</td><td>{num(c.quote.ltp)}</td><td>{num(c.quote.bid)}/{num(c.quote.ask)}</td><td>{num(c.spread_pct, 2)}</td>
                <td>{c.quote.volume?.toLocaleString("en-IN") ?? "—"}</td><td>{c.lot_size ?? "—"}</td>
                <td><Badge tone={c.quote.greeks_source === "feed" ? "green" : c.quote.greeks_source === "calculated" ? "amber" : "red"}>{c.quote.greeks_source || "unavailable"}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sel.candidates.map((c: any) => (
        <div key={"chk" + c.strike} className="mt-2 flex flex-wrap gap-1">
          <span className="muted text-xs">{c.moneyness} checks:</span>
          {Object.entries(c.checks).map(([k, v]) => <Badge key={k} tone={v ? "green" : "red"}>{v ? "✓" : "✗"} {k.replace(/_/g, " ")}</Badge>)}
          {c.quote.greeks_note && <div className="muted w-full text-xs">{c.quote.greeks_note}</div>}
        </div>
      ))}
    </Card>
  );
}

export default function GreeksPage() {
  const [index, setIndex] = useState("NIFTY");
  const { data, error } = usePoll<any>(`/api/greeks/${index}`, 5000, [index]);
  return (
    <div className="space-y-3">
      <div className="flex gap-1">{["NIFTY", "BANKNIFTY"].map((i) => <button key={i} className={index === i ? "btn-primary" : "btn-ghost"} onClick={() => setIndex(i)}>{i}</button>)}</div>
      <ErrorBox error={error} />
      {!data ? <Loading /> : !data.available ? <div className="muted text-sm">No option-chain data.</div> : (
        <>
          <div className="muted text-xs">Expiry {data.expiry} · chain received {time(data.received_at)}. {data.model_note} Selection never uses premium alone.</div>
          <div className="grid gap-3 xl:grid-cols-2">
            <Side title="Bullish → CE (ATM / 1 ITM)" sel={data.BULLISH} />
            <Side title="Bearish → PE (ATM / 1 ITM)" sel={data.BEARISH} />
          </div>
        </>
      )}
    </div>
  );
}
