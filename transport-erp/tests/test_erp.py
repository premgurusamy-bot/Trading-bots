import os
import sys
import types

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ERP_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("ERP_SCHEDULER", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OUTBOX_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CANVA_CLIENT_ID", raising=False)
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    from fastapi.testclient import TestClient
    from app import main, db
    db.reset_connection()
    with TestClient(main.app) as c:
        yield c
    db.reset_connection()


def test_rules_team_runs_without_api_key(client):
    from app import agents, db
    for k in agents.ROLES:
        run = agents.run_agent(k)
        assert run["status"] == "ok", run
        assert run["mode"] == "rules"
    # accountant invoiced the delivered demo trip and flagged the old invoice overdue
    assert db.q1("SELECT * FROM invoices WHERE trip_id IS NOT NULL")["status"] == "unpaid"
    assert db.q1("SELECT status FROM invoices WHERE number='INV-0001'")["status"] == "overdue"
    assert db.q1("SELECT COUNT(*) n FROM outbox WHERE status='pending_approval'")["n"] >= 1
    # team leader dispatched a pending trip
    assert db.q1("SELECT COUNT(*) n FROM trips WHERE status='assigned'")["n"] >= 1
    # HR / compliance alerts from the demo data's near expiries
    cats = {a["category"] for a in db.q("SELECT category FROM alerts")}
    assert {"hr", "compliance"} <= cats
    # website + lead follow-up
    assert client.get("/site").status_code == 200
    assert db.q1("SELECT COUNT(*) n FROM tasks WHERE assigned_role='worker'")["n"] >= 1


def test_trip_delivery_auto_invoices_and_frees_vehicle(client):
    from app import agents, db
    agents.run_agent("team_leader")
    trip = db.q1("SELECT * FROM trips WHERE status='assigned'")
    client.put(f"/api/t/trips/{trip['id']}", json={"status": "delivered"})
    assert db.get("vehicles", trip["vehicle_id"])["status"] == "available"
    inv = db.q1("SELECT * FROM invoices WHERE trip_id=?", (trip["id"],))
    assert inv and inv["total"] == pytest.approx(trip["freight_amount"] * 1.18)


def test_content_approval_flow_and_public_lead(client):
    from app import db
    cid = client.post("/api/t/content", json={"kind": "social_post", "platform": "facebook", "title": "Hi", "body": "We deliver", "status": "pending_approval"}).json()["id"]
    client.post(f"/api/content/{cid}/approve")
    assert db.get("content", cid)["status"] == "published"
    assert db.q1("SELECT status FROM outbox WHERE ref=?", (f"content:{cid}",))["status"] == "approved"
    assert client.post("/api/public/lead", json={"name": "A", "phone": "1", "requirement": "x"}).status_code == 200


def test_sanitize():
    from app.agents import sanitize_html
    out = sanitize_html('<h1 onclick="x()">Hi</h1><script>alert(1)</script><a href="javascript:evil()">l</a>')
    assert "script" not in out and "onclick" not in out and "javascript" not in out


def test_ai_loop_with_fake_client(client, monkeypatch):
    """Simulates Claude calling tools: draft content then finish."""
    from app import agents, db
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    def blk(**kw):
        return types.SimpleNamespace(**kw)

    replies = [
        blk(stop_reason="tool_use", usage=blk(input_tokens=10, output_tokens=5), content=[
            blk(type="tool_use", id="t1", name="draft_content", input={"kind": "social_post", "platform": "linkedin", "title": "Reliable FTL", "body": "Book now"}),
            blk(type="tool_use", id="t2", name="query", input={"table": "nope"}),
        ]),
        blk(stop_reason="end_turn", usage=blk(input_tokens=10, output_tokens=5), content=[blk(type="text", text="Drafted one LinkedIn post.")]),
    ]
    sent = []

    class FakeMessages:
        def create(self, **kw):
            sent.append(kw)
            return replies[len(sent) - 1]

    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda: types.SimpleNamespace(beta=types.SimpleNamespace(messages=FakeMessages()))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    run = agents.run_agent("content_creator")
    assert run["mode"] == "ai:claude" and run["status"] == "ok"
    assert "Drafted one LinkedIn post." in run["summary"]
    assert db.q1("SELECT * FROM content WHERE title='Reliable FTL'")["status"] == "draft"
    tool_results = next(m["content"] for m in sent[1]["messages"] if m["role"] == "user" and isinstance(m["content"], list))
    assert tool_results[1]["is_error"] is True  # bad table name reported back to the model
    from app import llm
    assert sent[0]["model"] == llm.CLAUDE_MODEL and sent[0]["fallbacks"] == "default"


def _fake_openai(monkeypatch, responses, images=None):
    """Install a fake `openai` module that replays Responses API outputs."""
    sent = []

    class FakeResponses:
        def create(self, **kw):
            sent.append({**kw, "input": list(kw["input"])})
            return responses[sum(1 for x in sent if "input" in x) - 1]

    class FakeImages:
        def generate(self, **kw):
            sent.append(kw)
            import base64
            return types.SimpleNamespace(data=[types.SimpleNamespace(b64_json=base64.b64encode(b"\x89PNG fake").decode())])

    fake = types.ModuleType("openai")
    fake.OpenAI = lambda: types.SimpleNamespace(responses=FakeResponses(), images=FakeImages())
    monkeypatch.setitem(sys.modules, "openai", fake)
    return sent


def _resp(output, text=""):
    return types.SimpleNamespace(output=output, output_text=text, usage=types.SimpleNamespace(input_tokens=7, output_tokens=3))


def test_chatgpt_agent_with_image_tool(client, monkeypatch):
    from app import agents, db
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    call = types.SimpleNamespace(type="function_call", call_id="c1", name="generate_image",
                                 arguments='{"name": "Truck banner", "prompt": "blue truck at sunrise"}')
    sent = _fake_openai(monkeypatch, [_resp([call]), _resp([], "Made one image.")])
    db.execute("UPDATE agents SET provider='openai' WHERE key='designer'")
    run = agents.run_agent("designer")
    assert run["mode"] == "ai:openai" and run["status"] == "ok", run
    assert run["summary"] == "Made one image."
    d = db.q1("SELECT * FROM designs WHERE name='Truck banner'")
    assert d and d["image_path"] and client.get(f"/api/designs/{d['id']}.png").content.startswith(b"\x89PNG")
    second = sent[2]["input"]  # call 0 = responses, 1 = images, 2 = responses with tool output
    assert any(isinstance(i, dict) and i.get("type") == "function_call_output" and i["call_id"] == "c1" for i in second)
    assert sent[0]["tools"][0]["strict"] is False


def test_chat_endpoint_uses_chosen_provider(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    _fake_openai(monkeypatch, [_resp([], "Revenue this month is 28,000.")])
    r = client.post("/api/chat", json={"role": "general_manager", "provider": "openai", "messages": [{"role": "user", "content": "How is business?"}]})
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "openai" and "28,000" in r.json()["reply"]
    assert client.post("/api/chat", json={"messages": []}).status_code == 400


def test_chat_without_keys_is_clear(client):
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 400 and "API" in r.json()["detail"]


def test_provider_resolution(monkeypatch):
    from app import llm
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert llm.resolve("claude") == "openai"  # falls back to the configured one
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    assert llm.resolve("auto") == "claude" and llm.resolve("openai") == "openai"


def test_png_upload_and_canva_roundtrip(client, monkeypatch):
    from app import canva, db
    did = client.post("/api/t/designs", json={"name": "Poster", "kind": "banner", "svg": "<svg xmlns='http://www.w3.org/2000/svg'/>"}).json()["id"]
    import base64
    png = b"\x89PNG\r\n\x1a\nrest"
    assert client.post(f"/api/designs/{did}/png", json={"data_url": "data:image/png;base64," + base64.b64encode(png).decode()}).status_code == 200
    assert client.post(f"/api/designs/{did}/png", json={"data_url": "data:image/png;base64,AAAA"}).status_code == 400
    # Canva not connected -> clear error
    assert client.post(f"/api/designs/{did}/canva").status_code == 400
    calls = []

    def fake_api(method, path, body=None, raw=None, headers=None):
        calls.append((method, path, body, headers))
        if path == "/asset-uploads":
            return {"job": {"id": "j1", "status": "success", "asset": {"id": "A1"}}}
        if path == "/designs":
            return {"design": {"id": "D1", "urls": {"edit_url": "https://canva.example/edit", "view_url": "v"}}}
        if path == "/exports":
            return {"job": {"id": "e1", "status": "success", "urls": ["https://canva.example/x.png"]}}
        raise AssertionError(path)

    monkeypatch.setattr(canva, "_api", fake_api)
    r = client.post(f"/api/designs/{did}/canva").json()
    assert r["canva_design_id"] == "D1" and r["canva_edit_url"].endswith("/edit")
    assert calls[1][2]["asset_id"] == "A1" and calls[1][2]["design_type"]["type"] == "custom"
    assert "name_base64" in calls[0][3]["Asset-Upload-Metadata"]
    import io, urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: io.BytesIO(b"\x89PNG from canva"))
    client.post("/api/canva/import", json={"design_id": did})
    assert open(db.get("designs", did)["image_path"], "rb").read() == b"\x89PNG from canva"


def test_canva_oauth_url_and_state_check(client, monkeypatch):
    from app import canva
    monkeypatch.setenv("CANVA_CLIENT_ID", "cid")
    monkeypatch.setenv("CANVA_CLIENT_SECRET", "sec")
    r = client.get("/api/canva/connect", follow_redirects=False)
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert loc.startswith(canva.AUTH_URL) and "code_challenge_method=s256" in loc and "client_id=cid" in loc
    bad = client.get("/api/canva/callback?code=x&state=wrong")
    assert "expired or invalid" in bad.text


def test_password_protects_api(tmp_path, monkeypatch):
    monkeypatch.setenv("ERP_DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setenv("ERP_SCHEDULER", "0")
    monkeypatch.setenv("ERP_PASSWORD", "secret123")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    from fastapi.testclient import TestClient
    from app import main, db
    db.reset_connection()
    with TestClient(main.app) as c:
        assert c.get("/api/dashboard").status_code == 401
        assert c.get("/api/ping").status_code == 200 and c.get("/site").status_code == 200
        assert c.post("/api/login", json={"password": "nope"}).status_code == 401
        assert c.post("/api/login", json={"password": "secret123"}).status_code == 200
        assert c.get("/api/dashboard").status_code == 200
    db.reset_connection()
