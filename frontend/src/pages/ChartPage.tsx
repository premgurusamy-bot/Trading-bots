import { useEffect, useRef, useState } from "react";
import { createChart, CrosshairMode, IChartApi, LineStyle, UTCTimestamp } from "lightweight-charts";
import { Badge, Card, ErrorBox, usePoll } from "../components/ui";

// lightweight-charts renders in UTC; shift by +5:30 so the axis shows IST.
const IST_OFFSET = 19800;

const LEVEL_COLORS: Record<string, string> = {
  prev_day: "#a855f7", opening_range: "#f59e0b", swing: "#64748b", zone: "#14b8a6", fib: "#eab308",
  oi: "#ec4899", trendline: "#22d3ee", vwap: "#3b82f6", ema20: "#f97316",
};

export default function ChartPage() {
  const [index, setIndex] = useState("NIFTY");
  const [tf, setTf] = useState(5);
  const [showLevels, setShowLevels] = useState(true);
  const { data, error } = usePoll<any>(`/api/chart/${index}?tf=${tf}`, 5000, [index, tf]);
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!ref.current || !data) return;
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const chart = createChart(ref.current, {
      height: Math.max(360, Math.min(640, window.innerHeight - 220)),
      layout: { background: { color: dark ? "#0f172a" : "#ffffff" }, textColor: dark ? "#cbd5e1" : "#334155" },
      grid: { vertLines: { color: dark ? "#1e293b" : "#f1f5f9" }, horzLines: { color: dark ? "#1e293b" : "#f1f5f9" } },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false },
      autoSize: true,
    });
    chartRef.current = chart;
    const cs = data.candles.map((c: any) => ({ time: (c.t + IST_OFFSET) as UTCTimestamp, open: c.o, high: c.h, low: c.l, close: c.c }));
    const candles = chart.addCandlestickSeries({ upColor: "#16a34a", downColor: "#dc2626", wickUpColor: "#16a34a", wickDownColor: "#dc2626", borderVisible: false });
    candles.setData(cs);
    const istDay = (t: number) => Math.floor(t / 86400);
    const line = (vals: (number | null)[], color: string, title: string, breakEachSession = false) => {
      const s = chart.addLineSeries({ color, lineWidth: 2, title, priceLineVisible: false, lastValueVisible: false });
      // A whitespace point (time only) breaks the line: used for session-anchored VWAP and for missing values.
      s.setData(vals.map((v, i) => {
        const newSession = breakEachSession && i > 0 && istDay(cs[i].time as number) !== istDay(cs[i - 1].time as number);
        return v === null || newSession ? { time: cs[i].time } : { time: cs[i].time, value: v };
      }) as any);
    };
    line(data.ema9, "#22c55e", "EMA9");
    line(data.ema20, "#f97316", "EMA20");
    line(data.vwap, "#3b82f6", "VWAP", true);
    const vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", color: "#94a3b8" });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    vol.setData(data.candles.map((c: any, i: number) => ({ time: cs[i].time, value: c.v, color: c.c >= c.o ? "#86efac" : "#fca5a5" })));
    if (showLevels) {
      for (const l of data.levels || []) {
        if (["vwap", "ema20"].includes(l.family)) continue;
        candles.createPriceLine({ price: l.price, color: LEVEL_COLORS[l.family] || "#94a3b8", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: l.source });
      }
    }
    const markers: any[] = [];
    for (const s of data.signals || []) {
      const t = Math.floor(new Date(s.confirm_ts).getTime() / 1000) + IST_OFFSET;
      markers.push({ time: t, position: s.direction === "BULLISH" ? "belowBar" : "aboveBar", color: s.direction === "BULLISH" ? "#16a34a" : "#dc2626",
        shape: s.direction === "BULLISH" ? "arrowUp" : "arrowDown", text: `${s.setup} ${s.score.total} ${s.state}` });
      for (const [k, c] of [["trigger", "#3b82f6"], ["stop", "#dc2626"], ["t1", "#16a34a"], ["t2", "#16a34a"], ["t3", "#16a34a"]] as const) {
        if (s.plan[k]) candles.createPriceLine({ price: s.plan[k], color: c, lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: `${k.toUpperCase()}` });
      }
    }
    candles.setMarkers(markers.sort((a, b) => a.time - b.time));
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [data, showLevels]);

  return (
    <div className="space-y-3">
      <Card
        title="Technical chart"
        right={
          <div className="flex flex-wrap gap-1">
            {["NIFTY", "BANKNIFTY"].map((i) => <button key={i} className={index === i ? "btn-primary" : "btn-ghost"} onClick={() => setIndex(i)}>{i}</button>)}
            {[1, 5, 15].map((t) => <button key={t} className={tf === t ? "btn-primary" : "btn-ghost"} onClick={() => setTf(t)}>{t}m</button>)}
            <button className="btn-ghost" onClick={() => setShowLevels(!showLevels)}>{showLevels ? "Hide" : "Show"} levels</button>
          </div>
        }
      >
        <ErrorBox error={error} />
        {data && data.candles.length === 0 && <div className="muted p-6 text-center text-sm">No candle data. Check Health → feed status.</div>}
        <div ref={ref} className="w-full" />
        <div className="muted mt-2 text-xs">
          Source: {data?.source || "—"}. {data?.note} Levels are from the engine's last evaluation (5m). Volume = current-month futures volume (index spot has none).
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
          {Object.entries(LEVEL_COLORS).map(([k, c]) => <Badge key={k}><span style={{ color: c }}>■</span> {k}</Badge>)}
        </div>
      </Card>
      {data?.levels?.length > 0 && (
        <Card title="Support / resistance levels (inspectable)">
          <div className="max-h-80 overflow-auto">
            <table className="tbl">
              <thead><tr><th>Source</th><th>Family</th><th>Kind</th><th className="text-right">Price</th><th>Zone</th><th>Since</th><th>Confirmed</th><th className="text-right">Strength</th></tr></thead>
              <tbody>
                {data.levels.map((l: any, i: number) => (
                  <tr key={i}><td>{l.source}</td><td>{l.family}</td><td>{l.kind}</td><td className="text-right tabular-nums">{l.price.toFixed(2)}</td>
                    <td className="tabular-nums">{l.low !== l.high ? `${l.low.toFixed(1)}–${l.high.toFixed(1)}` : ""}</td><td>{l.ts?.slice(5, 16)}</td><td>{l.confirmed ? "yes" : "no"}</td><td className="text-right">{l.strength}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
