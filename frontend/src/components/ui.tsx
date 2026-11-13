import { ReactNode, useEffect, useState } from "react";
import { api } from "../lib/api";

export function Card({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <div className="mb-2 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">{title}</h2>
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

const TONES: Record<string, string> = {
  green: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  red: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  blue: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  gray: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
};

export function Badge({ tone = "gray", children }: { tone?: keyof typeof TONES | string; children: ReactNode }) {
  return <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${TONES[tone] || TONES.gray}`}>{children}</span>;
}

export function stateTone(s?: string | null) {
  if (!s) return "gray";
  if (["BULLISH", "EXECUTED", "OPEN", "FILLED", "CLOSED", "ok", "STRONG"].includes(s)) return "green";
  if (["BEARISH", "CANCELLED", "REJECTED", "ENTRY_FAILED", "AVOID", "invalid"].includes(s)) return "red";
  if (["PENDING", "TRIGGERED", "SIDEWAYS", "WAIT", "AWAITING_CONFIRMATION", "CLOSING", "PENDING_ENTRY", "EXPIRED"].includes(s)) return "amber";
  if (["VALID"].includes(s)) return "blue";
  return "gray";
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="muted p-4 text-sm">{what}…</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="rounded-lg border border-red-300 bg-red-50 p-2 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">{String((error as any)?.message ?? error)}</div>;
}

export function Kv({ k, v }: { k: ReactNode; v: ReactNode }) {
  return (
    <div className="flex justify-between gap-2 py-0.5 text-sm">
      <span className="muted">{k}</span>
      <span className="text-right font-medium tabular-nums">{v}</span>
    </div>
  );
}

/** Poll an API endpoint. */
export function usePoll<T = any>(path: string | null, ms = 3000, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const load = () =>
      api<T>(path)
        .then((d) => alive && (setData(d), setError(null)))
        .catch((e) => alive && setError(e));
    load();
    const id = window.setInterval(load, ms);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ms, tick, ...deps]);
  return { data, error, reload: () => setTick((t) => t + 1) };
}

export function JsonBlock({ value }: { value: unknown }) {
  return <pre className="max-h-96 overflow-auto rounded bg-slate-100 p-2 text-[11px] dark:bg-slate-800">{JSON.stringify(value, null, 2)}</pre>;
}

export function ScoreBreakdown({ score }: { score: any }) {
  if (!score) return <div className="muted text-sm">No candidate on the last completed candle.</div>;
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-2xl font-bold tabular-nums">{score.total}</span>
        <span className="muted">/ 100</span>
        <Badge tone={stateTone(score.band)}>{score.band}</Badge>
      </div>
      <table className="tbl">
        <thead>
          <tr>
            <th>Component</th>
            <th className="text-right">Pts</th>
            <th>Reasons</th>
          </tr>
        </thead>
        <tbody>
          {score.components.map((c: any) => (
            <tr key={c.key}>
              <td className="whitespace-nowrap">
                {c.name} {!c.data_ok && <Badge tone="amber">data</Badge>}
              </td>
              <td className="text-right tabular-nums">
                {c.points}/{c.max}
              </td>
              <td className="muted">{c.reasons.join("; ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Mandatory({ m }: { m?: Record<string, any> }) {
  if (!m) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {Object.entries(m).map(([k, v]) => (
        <Badge key={k} tone={v === true ? "green" : v === false ? "red" : "gray"}>
          {v === true ? "✓" : v === false ? "✗" : "–"} {k.replace(/_/g, " ")}
        </Badge>
      ))}
    </div>
  );
}

export function ConfirmButton({
  label,
  onConfirm,
  danger,
  prompt,
  requireText,
}: {
  label: string;
  onConfirm: (text?: string) => Promise<unknown> | void;
  danger?: boolean;
  prompt: string;
  requireText?: string;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <>
      <button className={danger ? "btn-danger" : "btn-primary"} onClick={() => setOpen(true)}>
        {label}
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog">
          <div className="card w-full max-w-md">
            <p className="mb-3 text-sm">{prompt}</p>
            {requireText && (
              <>
                <label className="label">Type exactly: {requireText}</label>
                <input className="input mb-3" value={text} onChange={(e) => setText(e.target.value)} />
              </>
            )}
            <ErrorBox error={err} />
            <div className="mt-3 flex justify-end gap-2">
              <button className="btn-ghost" onClick={() => setOpen(false)}>
                Cancel
              </button>
              <button
                className={danger ? "btn-danger" : "btn-primary"}
                disabled={busy || (!!requireText && text !== requireText)}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await onConfirm(text);
                    setOpen(false);
                  } catch (e) {
                    setErr(e);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
