"""API security/behaviour and backtest no-look-ahead (SYNTHETIC data, no network)."""
import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

os.environ["APP_FACTORY"] = "0"
from app.main import create_app  # noqa: E402
from app.backtest.metrics import trade_metrics  # noqa: E402
from app.backtest.sim import run_backtest  # noqa: E402
from app.strategy.params import StrategyParams  # noqa: E402
from tests.synthetic import path_candles, uptrend_closes  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DHAN_CLIENT_ID", raising=False)
    monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
    app = create_app(f"sqlite:///{(tmp_path / 'api.db').as_posix()}", start_feed=False, start_loop=False)
    with TestClient(app) as c:
        yield c


def login(c):
    assert c.post("/api/auth/setup", json={"password": "correct horse"}).status_code == 200
    tok = c.post("/api/auth/login", json={"password": "correct horse"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_health_public_everything_else_requires_login(client):
    assert client.get("/api/health").status_code == 200
    for path in ("/api/dashboard", "/api/settings", "/api/credentials", "/api/trades", "/api/system"):
        assert client.get(path).status_code == 401
    assert client.post("/api/auth/login", json={"password": "whatever123"}).status_code == 401


def test_first_run_and_dashboard_shows_disconnected_feed(client):
    h = login(client)
    assert client.post("/api/auth/setup", json={"password": "another pass"}).status_code == 409
    d = client.get("/api/dashboard", headers=h).json()
    assert d["mode"] == "PAPER" and d["live_enabled"] is False
    assert d["feed"]["connected"] is False and d["feed"]["faults"]
    assert d["indices"]["NIFTY"]["ltp"] is None          # nothing fabricated
    assert client.get("/api/chain/NIFTY", headers=h).json()["available"] is False


def test_credentials_never_returned(client, monkeypatch):
    h = login(client)
    monkeypatch.setenv("DHAN_CLIENT_ID", "1100000001")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "SUPERSECRETTOKENVALUE123")
    body = client.get("/api/credentials", headers=h).text
    assert "SUPERSECRET" not in body and "0001" in body
    assert "SUPERSECRET" not in client.get("/api/system", headers=h).text


def test_frozen_values_need_explicit_text_and_are_audited(client):
    h = login(client)
    r = client.put("/api/settings/strategy", headers=h, json={"changes": {"min_score": 70}, "confirm": True})
    assert r.status_code == 400 and "frozen" in r.json()["detail"]
    r = client.put("/api/settings/strategy", headers=h, json={"changes": {"adx_min_trend": 25}, "confirm": False})
    assert r.status_code == 400
    r = client.put("/api/settings/strategy", headers=h, json={"changes": {"adx_min_trend": 25}, "confirm": True})
    assert r.status_code == 200 and "adx_min_trend" in r.json()["defaults_changed"]
    s = client.get("/api/settings", headers=h).json()
    assert s["params"]["adx_min_trend"] == 25 and s["params"]["min_score"] == 75
    assert any(a["action"] == "strategy_params_changed" for a in client.get("/api/audit", headers=h).json())


def test_live_enable_blocked_without_feature_flag(client):
    h = login(client)
    ack = "I UNDERSTAND LIVE TRADING PLACES REAL ORDERS WITH REAL MONEY"
    r = client.post("/api/mode/live", headers=h, json={"acknowledgement": ack, "password": "correct horse"})
    assert r.status_code == 400 and "FEATURE_FLAG" in r.json()["detail"]
    r = client.post("/api/mode/live", headers=h, json={"acknowledgement": ack, "password": "wrong password"})
    assert r.status_code == 401
    assert client.get("/api/dashboard", headers=h).json()["mode"] == "PAPER"


def test_kill_switch_requires_confirmation(client):
    h = login(client)
    assert client.post("/api/control/kill-switch", headers=h, json={"confirm": False}).status_code == 400
    assert client.post("/api/control/kill-switch", headers=h, json={"confirm": True}).status_code == 200
    assert client.get("/api/risk", headers=h).json()["flags"]["kill_switch"] is True


def test_websocket_rejects_without_token(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws") as ws:
            ws.receive_text()


# ---------------- backtest ----------------
def synthetic_days(n_days=4, hammer_bars=(24, 40, 52)):
    """SYNTHETIC uptrend days with hammer probes to EMA20 injected at `hammer_bars` of each day,
    followed by continuation, so that technical candidates and triggers occur."""
    from app.strategy import indicators as ind
    from tests.synthetic import c as mk, session_times
    out = []
    d = date(2026, 10, 5)
    px = 24000.0
    while len({c.ts.date() for c in out}) < n_days:
        if d.weekday() < 5:
            tms = session_times(d)
            closes = uptrend_closes(75, px, up=4, down=-2)
            prev = px
            for k, cl in enumerate(closes):
                if k in hammer_bars and len(out) > 25:
                    e20 = ind.ema([x.close for x in out], 20)[-1]
                    o = prev
                    cl2 = o + 2
                    out.append(mk(tms[k], o, cl2 + 0.5, min(e20 - 1.0, o - 1), cl2, 2500))
                    prev = cl2
                    continue
                cl = prev + (cl - (closes[k - 1] if k else px))
                o = prev
                out.append(mk(tms[k], o, max(o, cl) + 6, min(o, cl) - 6, cl, 1000))
                prev = cl
            px = prev
        d += timedelta(days=1)
    return out


def test_backtest_no_lookahead_prefix_invariance():
    p = StrategyParams()
    full = synthetic_days(4)
    cut = full[: 75 * 3]
    a = run_backtest("NIFTY", cut, p)["trades"]
    b = run_backtest("NIFTY", full, p)["trades"]
    last_ts = cut[-1].ts
    a_closed = [t.to_dict() for t in a if t.exit_ts and t.exit_ts < last_ts]
    b_same = [t.to_dict() for t in b if t.exit_ts and t.exit_ts < last_ts]
    assert a_closed, "synthetic data should produce at least one trade"
    assert a_closed == b_same     # appending future data never changes earlier results


def test_backtest_costs_with_premium_overlay():
    p = StrategyParams()
    cs = synthetic_days(3)
    prem = {"CE": {c.ts: 100.0 + (c.close - cs[0].close) * 0.5 for c in cs},
            "PE": {c.ts: max(5.0, 100.0 - (c.close - cs[0].close) * 0.5) for c in cs}}
    res = run_backtest("NIFTY", cs, p, prem, 65)
    assert res["trades"]
    for t in res["trades"]:
        if t.prem_entry and t.prem_exit:
            assert t.charges > 0 and abs(t.net - (t.gross - t.charges)) < 0.02


def test_metrics_basic_and_small_sample_flag():
    from datetime import datetime
    from app.core.types import IST
    tr = [{"net": v, "index": "NIFTY", "setup": "A", "opened_at": datetime(2026, 1, 1, 10, tzinfo=IST)} for v in (100, -50, 200, -50)]
    m = trade_metrics(tr)
    o = m["overall"]
    assert o["trades"] == 4 and o["win_rate"] == 50.0 and o["profit_factor"] == 3.0 and o["expectancy"] == 50.0
    assert o["max_drawdown"] == 50 and not o["sample_adequate"] and "warning" in m


def test_cors_allows_android_app_origin_only(client):
    ok = client.options("/api/health", headers={"Origin": "http://localhost", "Access-Control-Request-Method": "GET",
                                               "Access-Control-Request-Headers": "authorization"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost"
    bad = client.options("/api/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers
    assert client.get("/api/dashboard", headers={"Origin": "http://localhost"}).status_code == 401   # still needs login
