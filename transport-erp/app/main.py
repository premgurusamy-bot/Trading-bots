"""Transport ERP web server: REST API, AI-team scheduler, outbox dispatcher and public website."""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import threading
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

from fastapi import Body, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response  # noqa: E402

from . import agents, db  # noqa: E402

_stop = threading.Event()


@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    agents.ensure_agents()
    if os.environ.get("ERP_DEMO_DATA", "1") == "1":
        db.seed_demo()
    if os.environ.get("ERP_SCHEDULER", "1") == "1":
        threading.Thread(target=scheduler_loop, args=(_stop,), daemon=True).start()
    yield
    _stop.set()


app = FastAPI(title="Transport ERP", lifespan=lifespan)
STATIC = Path(__file__).resolve().parent / "static"
PASSWORD = os.environ.get("ERP_PASSWORD", "")


def _token() -> str:
    return hmac.new(PASSWORD.encode(), b"erp-session", hashlib.sha256).hexdigest()


@app.middleware("http")
async def auth(request: Request, call_next):
    path = request.url.path
    public = path in ("/", "/login", "/api/login", "/api/public/lead") or path.startswith("/site") or path.endswith(".svg")
    if PASSWORD and not public and request.cookies.get("erp") != _token():
        return JSONResponse({"detail": "login required"}, status_code=401)
    return await call_next(request)


@app.post("/api/login")
def login(data: dict = Body(...)):
    if not PASSWORD or hmac.compare_digest(str(data.get("password", "")), PASSWORD):
        r = JSONResponse({"ok": True})
        if PASSWORD:
            r.set_cookie("erp", _token(), httponly=True, samesite="strict", max_age=30 * 86400)
        return r
    raise HTTPException(401, "wrong password")


# ----------------------------------------------------------------------------- generic CRUD
def _table(name: str) -> str:
    if name not in db.TABLES:
        raise HTTPException(404, "unknown table")
    return name


@app.get("/api/meta")
def meta():
    return {
        "tables": {k: {"label": v["label"], "cols": {c: {"type": t, "options": o} for c, (t, o) in v["cols"].items()}} for k, v in db.TABLES.items()},
        "ai_enabled": agents.ai_available(), "model": agents.MODEL, "password": bool(PASSWORD),
        "webhook": bool(os.environ.get("OUTBOX_WEBHOOK_URL")),
    }


@app.get("/api/t/{table}")
def list_rows(table: str, status: str | None = None, limit: int = 500):
    t = _table(table)
    if status and "status" in db.TABLES[t]["cols"]:
        return db.q(f"SELECT * FROM {t} WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit))
    return db.q(f"SELECT * FROM {t} ORDER BY id DESC LIMIT ?", (limit,))


@app.post("/api/t/{table}")
def create_row(table: str, data: dict = Body(...)):
    t = _table(table)
    return db.get(t, db.insert(t, _coerce(t, data)))


@app.put("/api/t/{table}/{row_id}")
def update_row(table: str, row_id: int, data: dict = Body(...)):
    t = _table(table)
    before = db.get(t, row_id)
    if not before:
        raise HTTPException(404)
    data = _coerce(t, data)
    db.update(t, row_id, data)
    after = db.get(t, row_id)
    if t == "trips" and before["status"] != after["status"]:
        _trip_status_changed(after)
    return after


@app.delete("/api/t/{table}/{row_id}")
def delete_row(table: str, row_id: int):
    db.execute(f"DELETE FROM {_table(table)} WHERE id = ?", (row_id,))
    return {"ok": True}


def _coerce(table: str, data: dict) -> dict:
    out = {}
    for k, v in db.clean(table, data).items():
        t = db.TABLES[table]["cols"][k][0]
        if v == "" or v is None:
            out[k] = None
        elif t == "int" or t.startswith("ref:"):
            out[k] = int(float(v))
        elif t == "real":
            out[k] = float(v)
        else:
            out[k] = str(v)
    return out


def _trip_status_changed(trip: dict) -> None:
    """Keep the fleet in sync and invoice immediately on delivery."""
    if trip["status"] in ("delivered", "cancelled") and trip["vehicle_id"]:
        db.update("vehicles", trip["vehicle_id"], {"status": "available"})
    if trip["status"] == "delivered":
        if not trip["delivered_at"]:
            db.update("trips", trip["id"], {"delivered_at": db.today()})
        agents.make_invoice(trip["id"], "system")


# ----------------------------------------------------------------------------- dashboard, company, agents
@app.get("/api/dashboard")
def dashboard():
    return {
        "kpis": agents.kpis(),
        "alerts": db.q("SELECT * FROM alerts WHERE resolved = 0 ORDER BY CASE level WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, id DESC LIMIT 20"),
        "runs": db.q("SELECT * FROM agent_runs ORDER BY id DESC LIMIT 15"),
        "monthly": db.q("""SELECT substr(delivered_at,1,7) m, SUM(freight_amount) revenue, COUNT(*) trips FROM trips
                            WHERE status='delivered' AND delivered_at IS NOT NULL GROUP BY m ORDER BY m DESC LIMIT 6"""),
    }


@app.get("/api/company")
def get_company():
    return db.company()


@app.put("/api/company")
def put_company(data: dict = Body(...)):
    allowed = {"name", "tagline", "phone", "email", "address", "city", "services", "about", "gst_rate", "auto_publish", "auto_send_messages", "service_interval_km"}
    data = {k: v for k, v in data.items() if k in allowed}
    if data:
        db.execute(f"UPDATE company SET {', '.join(f'{k}=?' for k in data)} WHERE id=1", list(data.values()))
    return db.company()


@app.get("/api/agents")
def list_agents():
    rows = db.q("SELECT * FROM agents")
    for r in rows:
        r["tools"] = agents.ROLES[r["key"]]["tools"]
        r["last"] = db.q1("SELECT * FROM agent_runs WHERE agent_key=? ORDER BY id DESC LIMIT 1", (r["key"],))
        r["open_tasks"] = db.q1("SELECT COUNT(*) n FROM tasks WHERE assigned_role=? AND status IN ('open','in_progress')", (r["key"],))["n"]
    return sorted(rows, key=lambda r: list(agents.ROLES).index(r["key"]))


@app.put("/api/agents/{key}")
def put_agent(key: str, data: dict = Body(...)):
    if key not in agents.ROLES:
        raise HTTPException(404)
    for f in ("enabled", "interval_min", "mission"):
        if f in data:
            db.execute(f"UPDATE agents SET {f}=? WHERE key=?", (data[f], key))
    return db.q1("SELECT * FROM agents WHERE key=?", (key,))


_running: set[str] = set()


def _run_bg(key: str) -> None:
    if key in _running:
        return
    _running.add(key)
    try:
        agents.run_agent(key)
    finally:
        _running.discard(key)


@app.post("/api/agents/{key}/run")
def run_now(key: str):
    if key not in agents.ROLES:
        raise HTTPException(404)
    threading.Thread(target=_run_bg, args=(key,), daemon=True).start()
    return {"started": True}


@app.post("/api/agents/run-all")
def run_all():
    def go():
        for k in agents.ROLES:
            _run_bg(k)
    threading.Thread(target=go, daemon=True).start()
    return {"started": True}


@app.get("/api/runs")
def runs(agent: str | None = None, limit: int = 100):
    if agent:
        return db.q("SELECT * FROM agent_runs WHERE agent_key=? ORDER BY id DESC LIMIT ?", (agent, limit))
    return db.q("SELECT * FROM agent_runs ORDER BY id DESC LIMIT ?", (limit,))


# ----------------------------------------------------------------------------- approvals & outbox
@app.post("/api/outbox/{oid}/{action}")
def outbox_action(oid: int, action: str):
    if action not in ("approve", "reject", "retry"):
        raise HTTPException(400)
    db.update("outbox", oid, {"status": {"approve": "approved", "reject": "rejected", "retry": "approved"}[action], "error": None})
    if action != "reject":
        dispatch_outbox()
    return db.get("outbox", oid)


@app.post("/api/content/{cid}/{action}")
def content_action(cid: int, action: str):
    if action not in ("approve", "reject"):
        raise HTTPException(400)
    db.update("content", cid, {"status": "approved" if action == "approve" else "rejected"})
    if action == "approve":
        agents.rules_social_media_manager("social_media_manager")
        dispatch_outbox()
    return db.get("content", cid)


def dispatch_outbox() -> None:
    """Send approved Outbox items to the configured webhook (Zapier/Make/n8n deliver them)."""
    url = os.environ.get("OUTBOX_WEBHOOK_URL")
    if not url:
        return
    for item in db.q("SELECT * FROM outbox WHERE status='approved'"):
        payload = json.dumps({k: item[k] for k in ("id", "channel", "recipient", "subject", "body", "ref")}).encode()
        try:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=20).read()
            db.update("outbox", item["id"], {"status": "sent", "sent_at": db.now(), "error": None})
        except Exception as e:
            db.update("outbox", item["id"], {"status": "failed", "error": str(e)[:300]})


# ----------------------------------------------------------------------------- designs & website
@app.get("/api/designs/{did}.svg")
def design_svg(did: int):
    d = db.get("designs", did)
    if not d:
        raise HTTPException(404)
    return Response(d["svg"], media_type="image/svg+xml", headers={"Content-Security-Policy": "script-src 'none'"})


@app.get("/site", response_class=HTMLResponse)
def site():
    co = db.company()
    secs = db.q("SELECT * FROM website_sections ORDER BY position, id")
    nav = "".join(f"<a href='#{html.escape(s['key'])}'>{html.escape(s['title'] or s['key'])}</a>" for s in secs)
    body = "".join(f"<section id='{html.escape(s['key'])}'>{agents.sanitize_html(s['html'] or '')}</section>" for s in secs)
    name = html.escape(co.get("name") or "")
    form = """<section id='enquiry'><h2>Get a quote</h2><form onsubmit="event.preventDefault();fetch('/api/public/lead',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify(Object.fromEntries(new FormData(this)))}).then(()=>{this.innerHTML='<p>Thank you! We will call you shortly.</p>'})">
<input name=name placeholder='Your name' required><input name=company placeholder='Company'><input name=phone placeholder='Phone' required>
<input name=email placeholder='Email'><textarea name=requirement placeholder='From, to, goods, weight, how often' required></textarea><button>Send enquiry</button></form></section>"""
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>{name}</title><meta name=description content='{html.escape(co.get('tagline') or '')}'><style>
body{{margin:0;font-family:system-ui,Segoe UI,Roboto,sans-serif;color:#1d2433;background:#fff;line-height:1.55}}
header{{position:sticky;top:0;background:#0f3d6e;color:#fff;display:flex;flex-wrap:wrap;gap:16px;align-items:center;padding:12px 20px}}
header b{{font-size:1.15rem;margin-right:auto}} header a{{color:#fff;text-decoration:none;opacity:.9}}
section{{max-width:1000px;margin:0 auto;padding:40px 20px}} section:first-of-type{{padding-top:64px}}
h1{{font-size:clamp(2rem,5vw,3rem);margin:0 0 12px}} a{{color:#0f62c4}}
form{{display:grid;gap:10px;max-width:520px}} input,textarea,button{{font:inherit;padding:10px;border:1px solid #c8cfdb;border-radius:8px}}
button{{background:#f28c28;color:#fff;border:0;font-weight:600;cursor:pointer}} footer{{text-align:center;padding:24px;color:#667}}
</style></head><body><header><b>{name}</b>{nav}<a href='#enquiry'>Get a quote</a></header>{body}{form}
<footer>&copy; {name}</footer></body></html>"""


@app.post("/api/public/lead")
def public_lead(data: dict = Body(...)):
    data = {k: str(v)[:1000] for k, v in data.items() if k in ("name", "company", "phone", "email", "requirement")}
    if not data.get("name") or not (data.get("phone") or data.get("email")):
        raise HTTPException(400, "name and phone/email required")
    return {"id": db.insert("leads", {**data, "source": "website", "status": "new"})}


# ----------------------------------------------------------------------------- UI
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ----------------------------------------------------------------------------- scheduler
def scheduler_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            due = db.q("SELECT key FROM agents WHERE enabled=1 AND (next_run IS NULL OR next_run <= ?)", (db.now(),))
            for row in due:
                if stop.is_set():
                    break
                _run_bg(row["key"])
            dispatch_outbox()
        except Exception as e:  # never let the scheduler die
            print("scheduler error:", e)
        stop.wait(30)


def main():
    import uvicorn

    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "8100"))
    print(f"Transport ERP on http://{host}:{port}   AI: {'ON (' + agents.MODEL + ')' if agents.ai_available() else 'OFF - rules mode (add ANTHROPIC_API_KEY to .env)'}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
