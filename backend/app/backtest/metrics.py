"""Performance metrics (shared by journal analytics and backtests)."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

MIN_ADEQUATE_SAMPLE = 30


def _stats(vals: list[float]) -> dict:
    n = len(vals)
    if n == 0:
        return {"trades": 0}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v <= 0]
    eq, peak, mdd = 0.0, 0.0, 0.0
    streak = worst = 0
    for v in vals:
        eq += v
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
        streak = streak + 1 if v <= 0 else 0
        worst = max(worst, streak)
    gp, gl = sum(wins), -sum(losses)
    return {
        "trades": n, "wins": len(wins), "losses": len(losses), "win_rate": round(len(wins) / n * 100, 2),
        "avg_win": round(gp / len(wins), 2) if wins else 0.0, "avg_loss": round(-gl / len(losses), 2) if losses else 0.0,
        "expectancy": round(sum(vals) / n, 2), "profit_factor": round(gp / gl, 2) if gl > 0 else None,
        "max_drawdown": round(mdd, 2), "net": round(sum(vals), 2), "max_losing_streak": worst,
        "sample_adequate": n >= MIN_ADEQUATE_SAMPLE,
    }


def trade_metrics(trades: Iterable[dict], value_key: str = "net") -> dict:
    trades = list(trades)
    vals = [t[value_key] for t in trades]
    out = {"overall": _stats(vals), "costs": round(sum(t.get("charges", 0) or 0 for t in trades), 2),
           "gross": round(sum(t.get("gross", 0) or 0 for t in trades), 2)}
    for key in ("index", "setup"):
        g = defaultdict(list)
        for t in trades:
            g[t.get(key)].append(t[value_key])
        out[f"by_{key}"] = {str(k): _stats(v) for k, v in g.items()}
    g = defaultdict(list)
    for t in trades:
        ts = t.get("opened_at")
        if ts is not None:
            g[f"{ts.hour:02d}:00"].append(t[value_key])
    out["by_hour"] = {k: _stats(v) for k, v in sorted(g.items())}
    rr = [t.get("r_multiple") for t in trades if t.get("r_multiple") is not None]
    if rr:
        bins = defaultdict(int)
        for r in rr:
            b = max(-2, min(4, int(r // 1)))
            bins[f"{b}R..{b + 1}R"] += 1
        out["r_distribution"] = dict(sorted(bins.items()))
    if trades and len(trades) < MIN_ADEQUATE_SAMPLE:
        out["warning"] = f"Only {len(trades)} trades — sample too small for reliable conclusions (< {MIN_ADEQUATE_SAMPLE})."
    return out
