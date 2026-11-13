import { api } from "../lib/api";
import { Badge, Card, ConfirmButton, ErrorBox, Kv, Loading, usePoll } from "../components/ui";
import { inr, num } from "../lib/format";

export default function RiskPage() {
  const { data, error, reload } = usePoll<any>("/api/risk", 2000);
  if (error && !data) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const d = data.daily, l = data.limits, s = data.secure_exit_config;
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Card title="Daily limits & counters">
        <Kv k="NIFTY entries" v={`${d.entries.NIFTY} / ${l.max_entries_per_index_per_day}`} />
        <Kv k="BANK NIFTY entries" v={`${d.entries.BANKNIFTY} / ${l.max_entries_per_index_per_day}`} />
        <Kv k="Active NIFTY / BANK NIFTY" v={`${d.active.NIFTY ? "yes" : "no"} / ${d.active.BANKNIFTY ? "yes" : "no"}`} />
        <Kv k="Max active per index" v={l.max_active_positions_per_index} />
        <Kv k="Realised gross / net" v={`${inr(d.realised_gross, 2)} / ${inr(d.realised_net, 2)}`} />
        <Kv k="Per-trade max loss (gross)" v={inr(l.per_trade_max_loss)} />
        <Kv k="Daily max loss (gross)" v={inr(l.daily_max_loss)} />
        <Kv k="Entry cutoff / EOD square-off" v={`${l.entry_cutoff} / ${l.squareoff_time}`} />
        <Kv k="Blocked" v={d.blocked_reason || "no"} />
      </Card>
      <Card title="Secure-Exit (profit protection) layer">
        <p className="muted mb-2 text-xs">Separate from the strategy's technical SL and targets; thresholds on GROSS P&L. The floor can only rise.</p>
        <Kv k="Enabled" v={s.enabled ? "yes" : "no"} />
        <Kv k="Activation / second level" v={`${inr(s.activation_level)} / ${inr(s.second_level)}`} />
        <Kv k="Profit target (action)" v={`${inr(s.profit_target)} (${s.profit_target_action})`} />
        <Kv k="Trailing increment" v={inr(s.trailing_increment)} />
        <Kv k="Small capital (< ₹25k): activation / trail" v={`${inr(s.small_activation)} / ${inr(s.small_trail_distance)}`} />
        <div className="mt-2 text-xs font-semibold">Open trades</div>
        {data.open_trades.length === 0 ? <div className="muted text-sm">none</div> : data.open_trades.map((t: any) => (
          <div key={t.id} className="text-sm">{t.index} {t.state} · stop {num(t.stop)} · premSL {num(t.premium_sl)} · floor {inr(t.secure.floor)} · peak {inr(t.secure.peak_gross)} ({t.secure.mode})</div>
        ))}
      </Card>
      <Card title="Controls" className="md:col-span-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={data.flags.kill_switch ? "red" : "green"}>kill switch {data.flags.kill_switch ? "ENGAGED" : "off"}</Badge>
          <Badge tone={data.flags.entries_enabled ? "green" : "amber"}>entries {data.flags.entries_enabled ? "enabled" : "disabled"}</Badge>
          <Badge tone={data.flags.order_state_uncertain ? "red" : "green"}>order state {data.flags.order_state_uncertain ? "UNCERTAIN" : "ok"}</Badge>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <ConfirmButton danger label="KILL SWITCH" prompt="Block all new entries, cancel pending signals and send exits for all open positions?" onConfirm={async () => { await api("/api/control/kill-switch", { method: "POST", body: { confirm: true } }); reload(); }} />
          {data.flags.kill_switch && <ConfirmButton label="Reset kill switch" prompt="Allow new entries again?" onConfirm={async () => { await api("/api/control/kill-switch/reset", { method: "POST", body: { confirm: true } }); reload(); }} />}
          <button className="btn-ghost" onClick={async () => { await api("/api/control/entries", { method: "POST", body: { enabled: !data.flags.entries_enabled } }); reload(); }}>
            {data.flags.entries_enabled ? "Pause new entries" : "Resume new entries"}
          </button>
          {data.flags.order_state_uncertain && <ConfirmButton danger label="Clear uncertain state" requireText="I HAVE RECONCILED WITH DHAN" prompt="Only after checking orders and positions in the Dhan app."
            onConfirm={async (t) => { await api("/api/control/uncertain/clear", { method: "POST", body: { confirm: true, text: t } }); reload(); }} />}
        </div>
      </Card>
    </div>
  );
}
