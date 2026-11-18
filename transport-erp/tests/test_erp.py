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
    assert run["mode"] == "ai" and run["status"] == "ok"
    assert "Drafted one LinkedIn post." in run["summary"]
    assert db.q1("SELECT * FROM content WHERE title='Reliable FTL'")["status"] == "draft"
    tool_results = next(m["content"] for m in sent[1]["messages"] if m["role"] == "user" and isinstance(m["content"], list))
    assert tool_results[1]["is_error"] is True  # bad table name reported back to the model
    assert sent[0]["model"] == agents.MODEL and sent[0]["fallbacks"] == "default"
