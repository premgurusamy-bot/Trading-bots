export const inr = (v: number | null | undefined, dp = 0) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : "₹" + v.toLocaleString("en-IN", { maximumFractionDigits: dp, minimumFractionDigits: dp });

export const num = (v: number | null | undefined, dp = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : v.toLocaleString("en-IN", { maximumFractionDigits: dp, minimumFractionDigits: dp });

export const pct = (v: number | null | undefined, dp = 2) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(dp)}%`);

export const time = (iso?: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
};

export const dt = (iso?: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { dateStyle: "short", timeStyle: "medium", hour12: false, timeZone: "Asia/Kolkata" });
};

export const age = (s?: number | null) => (s === null || s === undefined ? "—" : s < 60 ? `${s.toFixed(0)}s` : `${(s / 60).toFixed(1)}m`);

export const pnlClass = (v?: number | null) => (v === null || v === undefined ? "" : v > 0 ? "text-green-600 dark:text-green-400" : v < 0 ? "text-red-600 dark:text-red-400" : "");
