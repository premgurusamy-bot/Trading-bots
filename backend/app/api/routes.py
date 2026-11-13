"""REST API. All routes except /api/health and /api/auth/* require a dashboard login."""
from __future__ import annotations

import csv
import io
from dataclasses import replace
from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, desc

from app import db as dbm
from app.api import auth
from app.config import settings, APP_VERSION
from app.core import secrets
from app.core.types import IST, OptionType
from app.risk.charges import ChargeSchedule
from app.risk.secure_exit import SecureExitConfig
from app.strategy import indicators as ind
from app.strategy.optionchain import analyse_chain, window_strikes, atm_strike, oi_walls, max_pain, pcr
from app.strategy.params import StrategyParams, STRATEGY_VERSION, SPEC_FIELDS
from app.strategy.selection import select_contract

router = APIRouter(prefix="/api")
RT = None   # set by main


def rt():
    return RT


# ---------------------------------------------------------------- auth
class PwIn(BaseModel):
    password: str = Field(min_length=8, max_length=200)


@router.get("/auth/state")
def auth_state():
    return {"password_set": auth.password_is_set(rt().Session), "app_version": APP_VERSION}


@router.post("/auth/setup")
def auth_setup(body: PwIn):
    if auth.password_is_set(rt().Session):
        raise HTTPException(409, "Password already set")
    auth.set_password(rt().Session, body.password)
    rt().svc.audit("first-run", "password_set", {})
    return {"ok": True}


@router.post("/auth/login")
def auth_login(body: PwIn, request: Request):
    tok = auth.login(rt().Session, body.password, request.client.host if request.client else "?", settings.session_secret_ttl_hours)
    if not tok:
        raise HTTPException(401, "Wrong password")
    return {"token": tok}


@router.post("/auth/logout")
def auth_logout(tok: str = Depends(auth.require_auth)):
    auth.logout(tok)
    return {"ok": True}


# ---------------------------------------------------------------- health (no auth: no sensitive data)
@router.get("/health")
def health():
    r = rt()
    db_ok = True
    try:
        with r.Session() as s:
            s.execute(select(1))
    except Exception:
        db_ok = False
    st = r.provider.status()
    return {"ok": db_ok and r.last_loop_error is None, "db_ok": db_ok, "app_version": APP_VERSION,
            "strategy_version": STRATEGY_VERSION, "mode": r.svc.mode, "feed_connected": st.connected,
            "loop_at": r.last_loop_at.isoformat() if r.last_loop_at else None}


# ---------------------------------------------------------------- helpers
def _age(ts: Optional[datetime], now: datetime) -> Optional[float]:
    return round((now - ts).total_seconds(), 1) if ts else None


def _session_state(now: datetime) -> str:
    if now.weekday() >= 5:
        return "CLOSED (weekend)"
    t = now.time()
    if t < datetime.strptime("09:15", "%H:%M").time():
        return "PRE-OPEN"
    if t >= datetime.strptime("15:30", "%H:%M").time():
        return "CLOSED"
    return "OPEN"


# ---------------------------------------------------------------- dashboard
@router.get("/dashboard", dependencies=[Depends(auth.require_auth)])
def dashboard():
    r = rt()
    svc = r.svc
    now = datetime.now(IST)
    st = r.provider.status()
    out = {"now": now.isoformat(), "session": _session_state(now), "mode": svc.mode, "live_enabled": svc.live_enabled,
           "feed": {"connected": st.connected, "source": st.source, "token_status": st.token_status, "faults": st.faults},
           "strategy_version": STRATEGY_VERSION, "indices": {}}
    for idx in ("NIFTY", "BANKNIFTY"):
        tick = r.provider.tick(idx)
        cur, _ = r.provider.chain(idx)
        c5 = [c for c in r.provider.candles(idx, 5) if c.complete]
        dp = r.provider.daily_prev(idx, now.date())
        ltp = tick.ltp if tick else None
        ev = svc.last_eval.get(idx) or {}
        cand = ev.get("candidate") or {}
        out["indices"][idx] = {
            "ltp": ltp, "ltp_source": tick.source if tick else None,
            "change": round(ltp - dp.close, 2) if (ltp and dp) else None,
            "change_pct": round((ltp - dp.close) / dp.close * 100, 2) if (ltp and dp) else None,
            "tick_age_s": _age(tick.received_at, now) if tick else None,
            "chain_age_s": _age(cur.received_at, now) if cur else None,
            "last_candle": (c5[-1].ts + timedelta(minutes=5)).isoformat() if c5 else None,
            "state": ev.get("state"), "trend": ev.get("trend"), "messages": ev.get("messages", []),
            "quality": ev.get("quality"), "risk_gate": ev.get("risk_gate"), "score": cand.get("score"),
            "candidate": {k: cand.get(k) for k in ("direction", "setup", "failed", "mandatory", "plan")} if cand else None,
            "entries_today": svc.daily.entries.get(idx, 0), "max_entries": svc.p.max_entries_per_index_per_day,
            "active_trade": svc.daily.active.get(idx), "evaluated_at": ev.get("evaluated_at"),
        }
    bal = svc.broker.available_balance() if svc.mode == "PAPER" else (svc.live_broker.available_balance() if svc.live_broker else None)
    used = sum((t.entry_avg or 0) * t.exit_state.qty_open for t in svc.trades.values() if t.state in ("OPEN", "CLOSING"))
    unreal = 0.0
    for t in svc.trades.values():
        q = r.provider.option_quote(t.option.get("security_id")) if t.entry_avg else None
        if q and q[0].bid:
            unreal += (q[0].bid - t.entry_avg) * t.exit_state.qty_open
    out["pnl"] = {"realised_gross": round(svc.daily.realised_gross, 2), "realised_net": round(svc.daily.realised_net, 2),
                  "unrealised_gross": round(unreal, 2), "available_balance": bal, "used_capital": round(used, 2)}
    out["risk"] = {"blocked_reason": svc.daily.blocked_reason, "kill_switch": svc.flags.kill_switch,
                   "order_state_uncertain": svc.flags.order_state_uncertain, "entries_enabled": svc.entries_enabled,
                   "daily_loss_limit": svc.secure_cfg.max_daily_loss}
    return out


# ---------------------------------------------------------------- chart
@router.get("/chart/{index}", dependencies=[Depends(auth.require_auth)])
def chart(index: str, tf: int = 5):
    r = rt()
    if index not in ("NIFTY", "BANKNIFTY") or tf not in (1, 5, 15):
        raise HTTPException(400, "bad index/tf")
    cs = r.provider.candles(index, tf)[-400:]
    closes = [c.close for c in cs]
    p = r.svc.p
    e9, e20 = ind.ema(closes, p.ema_fast), ind.ema(closes, p.ema_slow)
    vw = ind.session_vwap(cs)
    at = ind.atr(cs, p.atr_len)
    ev = r.svc.last_eval.get(index) or {}
    today = datetime.now(IST).date()
    sigs = [s.to_dict() for s in r.svc.book.signals.values() if s.index == index and s.confirm_ts.date() == today]
    trades = [{"id": t.id, "entry": t.entry_avg, "plan": t.exit_state.to_dict()} for t in r.svc.trades.values() if t.index == index]
    return {"index": index, "tf": tf, "source": cs[0].source if cs else None,
            "candles": [{"t": int(c.ts.timestamp()), "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume,
                         "complete": c.complete} for c in cs],
            "ema9": e9, "ema20": e20, "vwap": vw, "atr": at, "levels": ev.get("levels", []), "signals": sigs,
            "trades": trades, "note": "Only completed candles are used for decisions; the last candle may be forming."}


# ---------------------------------------------------------------- option chain
@router.get("/chain/{index}", dependencies=[Depends(auth.require_auth)])
def chain(index: str, window: int = 10):
    r = rt()
    cur, prev = r.provider.chain(index)
    now = datetime.now(IST)
    if cur is None:
        return {"available": False, "reason": "No option-chain data (feed not connected or not yet received)"}
    dp = r.provider.daily_prev(index, now.date())
    an = analyse_chain(cur, prev, r.svc.p.near_atm_pcr_strikes, dp.close if dp else None)
    ks = window_strikes(cur, max(1, min(window, 30)))
    rows = []
    for k in ks:
        ce, pe = cur.get(k, OptionType.CE), cur.get(k, OptionType.PE)
        rows.append({"strike": k, "atm": k == an.atm, "ce": ce.to_dict() if ce else None, "pe": pe.to_dict() if pe else None})
    age = _age(cur.received_at, now)
    return {"available": True, "index": index, "expiry": cur.expiry.isoformat(), "underlying": cur.underlying_ltp,
            "source": cur.source, "received_at": cur.received_at.isoformat(), "age_s": age,
            "stale": age is not None and age > r.svc.p.max_chain_age_s, "rows": rows, "analysis": an.to_dict()}


@router.get("/greeks/{index}", dependencies=[Depends(auth.require_auth)])
def greeks(index: str):
    r = rt()
    cur, _ = r.provider.chain(index)
    if cur is None:
        return {"available": False}
    now = datetime.now(IST)
    out = {"available": True, "expiry": cur.expiry.isoformat(), "received_at": cur.received_at.isoformat()}
    for d in ("BULLISH", "BEARISH"):
        out[d] = select_contract(cur, d, r.svc.p, now, now.date()).to_dict()
    for d in ("BULLISH", "BEARISH"):
        for c in out[d]["candidates"]:
            info = r.provider.instrument(c["quote"].get("security_id") or "")
            c["lot_size"] = info.lot_size if info else None
            c["trading_symbol"] = info.trading_symbol if info else None
    out["model_note"] = ("Greeks marked 'feed' come from Dhan. 'calculated' = Black-Scholes (r, q=0) fallback; "
                         "see note per contract. Missing inputs => not shown.")
    return out


# ---------------------------------------------------------------- signals / trades / research
@router.get("/signals", dependencies=[Depends(auth.require_auth)])
def signals(day: Optional[date] = None):
    day = day or datetime.now(IST).date()
    with rt().Session() as s:
        rows = s.execute(select(dbm.SignalRow).where(dbm.SignalRow.day == day).order_by(desc(dbm.SignalRow.created_at))).scalars().all()
        return [r.data | {"trade_id": r.trade_id} for r in rows]


@router.get("/research", dependencies=[Depends(auth.require_auth)])
def research(day: Optional[date] = None, limit: int = 200):
    q = select(dbm.ResearchLog).order_by(desc(dbm.ResearchLog.created_at)).limit(min(limit, 1000))
    if day:
        q = q.where(dbm.ResearchLog.day == day)
    with rt().Session() as s:
        return [{"id": r.id, "index": r.index, "day": r.day.isoformat(), "kind": r.kind, "score": r.score,
                 "reasons": r.reasons, "snapshot_ref": r.snapshot_ref, "premium_high_after": r.premium_high_after,
                 "premium_low_after": r.premium_low_after, "data": r.data, "created_at": r.created_at.isoformat()}
                for r in s.execute(q).scalars().all()]


def _trade_dict(r: dbm.TradeRow) -> dict:
    return {c.name: (getattr(r, c.name).isoformat() if isinstance(getattr(r, c.name), (datetime, date)) else getattr(r, c.name))
            for c in dbm.TradeRow.__table__.columns}


@router.get("/trades", dependencies=[Depends(auth.require_auth)])
def trades(day: Optional[date] = None, limit: int = 200):
    q = select(dbm.TradeRow).order_by(desc(dbm.TradeRow.opened_at)).limit(min(limit, 2000))
    if day:
        q = q.where(dbm.TradeRow.day == day)
    with rt().Session() as s:
        return [_trade_dict(r) for r in s.execute(q).scalars().all()]


@router.get("/trades/{trade_id}", dependencies=[Depends(auth.require_auth)])
def trade(trade_id: str):
    with rt().Session() as s:
        r = s.get(dbm.TradeRow, trade_id)
        if r is None:
            raise HTTPException(404)
        orders = s.execute(select(dbm.OrderRow).where(dbm.OrderRow.trade_id == trade_id)).scalars().all()
        od = []
        for o in orders:
            fills = s.execute(select(dbm.FillRow).where(dbm.FillRow.order_id == o.id)).scalars().all()
            od.append({"client_order_id": o.client_order_id, "broker_order_id": o.broker_order_id, "side": o.side,
                       "qty": o.qty, "filled_qty": o.filled_qty, "avg_price": o.avg_price, "status": o.status,
                       "purpose": o.purpose, "reason": o.reason, "created_at": o.created_at.isoformat(),
                       "fills": [{"qty": f.qty, "price": f.price, "ts": f.ts.isoformat()} for f in fills]})
        sec = s.get(dbm.SecureExitRow, trade_id)
        return {"trade": _trade_dict(r), "orders": od, "secure_exit": sec.state if sec else None}


@router.get("/snapshot/{ref}", dependencies=[Depends(auth.require_auth)])
def snapshot(ref: str):
    with rt().Session() as s:
        r = s.get(dbm.DecisionSnapshot, ref)
        if r is None:
            raise HTTPException(404)
        return {"ref": r.ref, "index": r.index, "candle_ts": r.candle_ts.isoformat(), "strategy_version": r.strategy_version,
                "params_hash": r.params_hash, "snapshot": r.snapshot, "levels": r.levels, "evaluation": r.evaluation}


@router.get("/analytics", dependencies=[Depends(auth.require_auth)])
def analytics():
    with rt().Session() as s:
        closed = s.execute(select(dbm.TradeRow).where(dbm.TradeRow.state == "CLOSED")).scalars().all()
        research_rows = s.execute(select(dbm.ResearchLog)).scalars().all()
    from app.backtest.metrics import trade_metrics
    ex = trade_metrics([{"net": t.net_pnl, "gross": t.gross_pnl, "charges": t.charges, "index": t.index, "setup": t.setup,
                         "opened_at": t.opened_at, "mode": t.mode} for t in closed])
    by_kind: dict = {}
    by_reason: dict = {}
    for r in research_rows:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
        for reason in (r.reasons or []):
            key = reason if isinstance(reason, str) and len(reason) < 60 else str(reason)[:60]
            by_reason[key] = by_reason.get(key, 0) + 1
    return {"execution": ex, "research": {"by_kind": by_kind, "top_reasons": sorted(by_reason.items(), key=lambda x: -x[1])[:20],
                                          "count": len(research_rows)},
            "note": "Execution analytics include only executed trades. Rejected/expired candidates are analysed separately."}


@router.get("/export/journal.csv", dependencies=[Depends(auth.require_auth)])
def export_journal():
    with rt().Session() as s:
        rows = s.execute(select(dbm.TradeRow).order_by(dbm.TradeRow.opened_at)).scalars().all()
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["id", "mode", "day", "index", "direction", "setup", "lots", "qty", "entry_avg", "exit_avg", "gross_pnl",
            "charges", "net_pnl", "slippage", "exit_reason", "premium_high", "premium_low", "snapshot_ref", "opened_at", "closed_at"]
    w.writerow(cols)
    for r in rows:
        w.writerow([getattr(r, c) for c in cols])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=journal.csv"})


# ---------------------------------------------------------------- risk
@router.get("/risk", dependencies=[Depends(auth.require_auth)])
def risk():
    svc = rt().svc
    p = svc.p
    return {"daily": svc.daily.to_dict(), "secure_exit_config": svc.secure_cfg.to_dict(),
            "open_trades": [{"id": t.id, "index": t.index, "state": t.state, "secure": t.secure.to_dict(),
                             "stop": t.exit_state.stop, "premium_sl": t.exit_state.premium_sl, "qty_open": t.exit_state.qty_open}
                            for t in svc.trades.values()],
            "limits": {"max_entries_per_index_per_day": p.max_entries_per_index_per_day,
                       "max_active_positions_per_index": p.max_active_positions_per_index,
                       "entry_cutoff": p.entry_cutoff.strftime("%H:%M"), "squareoff_time": p.squareoff_time.strftime("%H:%M"),
                       "per_trade_max_loss": svc.secure_cfg.max_loss_per_trade, "daily_max_loss": svc.secure_cfg.max_daily_loss},
            "flags": {"kill_switch": svc.flags.kill_switch, "order_state_uncertain": svc.flags.order_state_uncertain,
                      "entries_enabled": svc.entries_enabled}}


# ---------------------------------------------------------------- controls
class Confirm(BaseModel):
    confirm: bool = False
    text: Optional[str] = None


@router.post("/control/kill-switch")
def kill(body: Confirm, tok: str = Depends(auth.require_auth)):
    if not body.confirm:
        raise HTTPException(400, "confirm required")
    rt().svc.kill_switch("dashboard")
    return {"ok": True}


@router.post("/control/kill-switch/reset")
def kill_reset(body: Confirm, tok: str = Depends(auth.require_auth)):
    if not body.confirm:
        raise HTTPException(400, "confirm required")
    rt().svc.reset_kill_switch("dashboard")
    return {"ok": True}


@router.post("/control/uncertain/clear")
def clear_uncertain(body: Confirm, tok: str = Depends(auth.require_auth)):
    if not body.confirm or body.text != "I HAVE RECONCILED WITH DHAN":
        raise HTTPException(400, "Type: I HAVE RECONCILED WITH DHAN")
    rt().svc.flags.order_state_uncertain = False
    rt().svc.audit("dashboard", "uncertain_cleared", {})
    return {"ok": True}


class Entries(BaseModel):
    enabled: bool


@router.post("/control/entries")
def entries(body: Entries, tok: str = Depends(auth.require_auth)):
    rt().svc.entries_enabled = body.enabled
    rt().svc.audit("dashboard", "entries_enabled" if body.enabled else "entries_disabled", {})
    return {"ok": True}


class LiveEnable(BaseModel):
    acknowledgement: str
    password: str


@router.post("/mode/live")
def mode_live(body: LiveEnable, tok: str = Depends(auth.require_auth)):
    if not auth.verify(rt().Session, body.password):
        raise HTTPException(401, "Password required to enable live trading")
    ok, msg = rt().enable_live("dashboard", body.acknowledgement)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg}


@router.post("/mode/paper")
def mode_paper(tok: str = Depends(auth.require_auth)):
    ok, msg = rt().disable_live("dashboard")
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg}


@router.get("/live/pending", dependencies=[Depends(auth.require_auth)])
def live_pending():
    return [{"trade_id": t.id, "summary": t.sizing.get("pretrade_summary"), "signal_id": t.signal_id}
            for t in rt().svc.trades.values() if t.state == "AWAITING_CONFIRMATION"]


@router.post("/live/confirm/{trade_id}")
def live_confirm(trade_id: str, body: Confirm, tok: str = Depends(auth.require_auth)):
    if not body.confirm:
        raise HTTPException(400, "confirm required")
    ok, msg = rt().svc.confirm_live_trade(trade_id, "dashboard")
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


class PaperReset(BaseModel):
    text: str
    capital: Optional[float] = Field(default=None, gt=0, le=1e9)


@router.post("/paper/reset")
def paper_reset(body: PaperReset, tok: str = Depends(auth.require_auth)):
    ok, msg = rt().svc.reset_paper("dashboard", body.text, body.capital)
    if not ok:
        raise HTTPException(400, msg)
    rt().put_setting("paper_account", {"capital": rt().svc.paper_capital, "cash": rt().svc.broker.available_balance()},
                     "dashboard", "paper_capital_set")
    return {"ok": True}


# ---------------------------------------------------------------- settings
@router.get("/settings", dependencies=[Depends(auth.require_auth)])
def get_settings():
    r = rt()
    return {"strategy_version": STRATEGY_VERSION, "params": r.svc.p.to_dict(), "spec_fields": sorted(SPEC_FIELDS),
            "defaults_changed": r.svc.p.defaults_changed(), "defaults": StrategyParams().to_dict(),
            "secure_exit": r.svc.secure_cfg.to_dict(), "secure_exit_defaults": SecureExitConfig().to_dict(),
            "charges": r.svc.schedule.to_dict(), "paper_capital": r.svc.paper_capital,
            "live_feature_flag": settings.live_trading_feature_flag, "market_data_mode": settings.market_data_mode,
            "app_version": APP_VERSION}


class StrategyChange(BaseModel):
    changes: dict
    confirm: bool = False
    spec_confirm_text: Optional[str] = None


@router.put("/settings/strategy")
def put_strategy(body: StrategyChange, tok: str = Depends(auth.require_auth)):
    r = rt()
    if not body.confirm:
        raise HTTPException(400, "Confirmation required for strategy changes")
    touched_spec = [k for k in body.changes if k in SPEC_FIELDS]
    if touched_spec and body.spec_confirm_text != "CHANGE FROZEN V1.0 VALUES":
        raise HTTPException(400, f"{touched_spec} are frozen V1.0 values. Type: CHANGE FROZEN V1.0 VALUES")
    if any(t.state in ("OPEN", "CLOSING", "PENDING_ENTRY") for t in r.svc.trades.values()):
        raise HTTPException(400, "Close open positions before changing strategy settings")
    try:
        merged = StrategyParams.from_dict({**r.svc.p.to_dict(), **body.changes})
    except Exception as e:
        raise HTTPException(400, f"Invalid value: {e}")
    if not (0 < merged.delta_min < merged.delta_max < 1) or merged.min_score > 100 or merged.min_score < 0:
        raise HTTPException(400, "Out-of-range value")
    r.put_setting("strategy_params", merged.to_dict(), "dashboard", "strategy_params_changed")
    r.svc.p = merged
    return {"ok": True, "defaults_changed": merged.defaults_changed()}


@router.post("/settings/strategy/reset-defaults")
def reset_strategy(body: Confirm, tok: str = Depends(auth.require_auth)):
    if not body.confirm:
        raise HTTPException(400, "confirm required")
    rt().put_setting("strategy_params", StrategyParams().to_dict(), "dashboard", "strategy_params_reset")
    rt().svc.p = StrategyParams()
    return {"ok": True}


class SecureChange(BaseModel):
    values: dict
    confirm: bool = False


@router.put("/settings/secure-exit")
def put_secure(body: SecureChange, tok: str = Depends(auth.require_auth)):
    if not body.confirm:
        raise HTTPException(400, "Confirmation required for risk-limit changes")
    r = rt()
    try:
        cfg = SecureExitConfig(**{**r.svc.secure_cfg.to_dict(), **body.values})
    except TypeError as e:
        raise HTTPException(400, str(e))
    if cfg.max_loss_per_trade <= 0 or cfg.max_daily_loss <= 0 or cfg.profit_target_action not in ("exit", "trail"):
        raise HTTPException(400, "Invalid secure-exit values")
    r.put_setting("secure_exit", cfg.to_dict(), "dashboard", "secure_exit_changed")
    r.svc.secure_cfg = cfg
    return {"ok": True}


class ChargesChange(BaseModel):
    schedule: dict
    confirm: bool = False


@router.put("/settings/charges")
def put_charges(body: ChargesChange, tok: str = Depends(auth.require_auth)):
    if not body.confirm:
        raise HTTPException(400, "confirm required")
    try:
        sched = ChargeSchedule.from_dict(body.schedule)
    except Exception as e:
        raise HTTPException(400, f"Invalid schedule: {e}")
    rt().put_setting("charges", sched.to_dict(), "dashboard", "charges_changed")
    rt().svc.schedule = sched
    return {"ok": True}


# ---------------------------------------------------------------- credentials (write-only)
class Creds(BaseModel):
    client_id: str = Field(min_length=3, max_length=64)
    access_token: str = Field(min_length=20, max_length=4096)


@router.get("/credentials", dependencies=[Depends(auth.require_auth)])
def creds_status():
    return secrets.status()


@router.post("/credentials")
def creds_set(body: Creds, tok: str = Depends(auth.require_auth)):
    try:
        secrets.store("dhan_client_id", body.client_id.strip())
        secrets.store("dhan_access_token", body.access_token.strip())
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    rt().svc.audit("dashboard", "credentials_changed", {"client_id": secrets.mask(body.client_id)})
    st = rt().restart_provider()
    return {"ok": True, "feed": st, "status": secrets.status()}


@router.delete("/credentials")
def creds_delete(tok: str = Depends(auth.require_auth)):
    for k in secrets.KEYS:
        secrets.delete(k)
    rt().svc.audit("dashboard", "credentials_deleted", {})
    rt().restart_provider()
    return {"ok": True}


@router.post("/feed/restart")
def feed_restart(tok: str = Depends(auth.require_auth)):
    return {"feed": rt().restart_provider()}


# ---------------------------------------------------------------- system
@router.get("/system", dependencies=[Depends(auth.require_auth)])
def system():
    r = rt()
    st = r.provider.status()
    now = datetime.now(IST)
    with r.Session() as s:
        evs = s.execute(select(dbm.SystemEvent).order_by(desc(dbm.SystemEvent.ts)).limit(100)).scalars().all()
        mv = s.execute(select(dbm.InstrumentMasterVersion).order_by(desc(dbm.InstrumentMasterVersion.id))).scalars().first()
    client = r.dhan_client
    return {"app_version": APP_VERSION, "strategy_version": STRATEGY_VERSION, "started_at": r.started_at.isoformat(),
            "loop_at": r.last_loop_at.isoformat() if r.last_loop_at else None, "loop_error": r.last_loop_error,
            "feed": {"connected": st.connected, "source": st.source, "token_status": st.token_status, "faults": st.faults,
                     "errors": st.errors[-20:], "rate_limited_until": st.rate_limited_until.isoformat() if st.rate_limited_until else None,
                     "last_tick_at": {k: v.isoformat() for k, v in st.last_tick_at.items()},
                     "last_chain_at": {k: v.isoformat() for k, v in st.last_chain_at.items()},
                     "last_candle_at": {k: v.isoformat() for k, v in st.last_candle_at.items()}},
            "dhan_api": {"last_ok_at": client.last_ok_at.isoformat() if client and client.last_ok_at else None,
                         "last_error": client.last_error if client else None},
            "credentials": secrets.status(),
            "instrument_master": {"version": mv.id, "fetched_at": mv.fetched_at.isoformat(), "rows": mv.row_count,
                                  "sha256": mv.sha256[:16]} if mv else None,
            "db": {"url": settings.database_url.split("///")[0] + "///…", "ok": True},
            "events": [{"ts": e.ts.isoformat(), "level": e.level, "source": e.source, "message": e.message} for e in evs]}


@router.get("/audit", dependencies=[Depends(auth.require_auth)])
def audit_log(limit: int = 200):
    with rt().Session() as s:
        rows = s.execute(select(dbm.AuditLog).order_by(desc(dbm.AuditLog.ts)).limit(min(limit, 1000))).scalars().all()
        return [{"ts": r.ts.isoformat(), "actor": r.actor, "action": r.action, "detail": r.detail} for r in rows]


@router.get("/events", dependencies=[Depends(auth.require_auth)])
def events():
    return list(rt().svc.events)[-200:]


# ---------------------------------------------------------------- backtest
class BTReq(BaseModel):
    index: str = "NIFTY"
    from_date: date
    to_date: date
    split: list[float] = [0.6, 0.2, 0.2]


@router.post("/backtest/run")
def bt_run(body: BTReq, tok: str = Depends(auth.require_auth)):
    from app.backtest.runner import run_from_dhan
    r = rt()
    if r.dhan_client is None:
        raise HTTPException(400, "Dhan data connection required for historical data")
    try:
        res = run_from_dhan(r, body.index, body.from_date, body.to_date, tuple(body.split))
    except Exception as e:
        raise HTTPException(400, f"Backtest failed: {type(e).__name__}: {e}")
    return res


@router.get("/backtest/runs", dependencies=[Depends(auth.require_auth)])
def bt_runs():
    with rt().Session() as s:
        rows = s.execute(select(dbm.BacktestRun).order_by(desc(dbm.BacktestRun.created_at)).limit(50)).scalars().all()
        return [{"id": r.id, "created_at": r.created_at.isoformat(), "kind": r.kind, "config": r.config,
                 "provenance": r.provenance, "results": r.results} for r in rows]
