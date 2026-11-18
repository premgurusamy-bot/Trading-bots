"""The AI team.

Every role runs automatically on its own schedule. A run has two layers:

1. RULES  - deterministic automation that always runs, even without an API key
            (auto-invoicing, dispatch, expiry alerts, payment reminders, publishing...).
2. AI     - if ANTHROPIC_API_KEY is set, Claude acts as that role: it reads ERP data and
            uses tools (create tasks for other roles, draft posts, design SVGs, edit the
            website, write reports...). Anything that leaves the company (emails, WhatsApp,
            social posts) goes to the Outbox and needs approval unless auto-send/auto-publish
            is switched on in Settings.
"""
from __future__ import annotations

import json
import os
import re
import traceback
from datetime import datetime, timedelta

from . import db

MODEL = os.environ.get("AI_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("AI_EFFORT", "medium")
MAX_STEPS = int(os.environ.get("AI_MAX_STEPS", "10"))

ROLES: dict[str, dict] = {
    "general_manager": {
        "name": "AI General Manager", "interval_min": 720,
        "mission": "Review the whole business (revenue, costs, fleet use, overdue money, alerts, open tasks). "
                   "Write a short daily management brief with the 3 most important actions, and delegate "
                   "concrete tasks to the right team members.",
        "tools": ["query", "kpis", "create_task", "write_report", "create_alert"],
    },
    "team_leader": {
        "name": "AI Team Leader (Operations)", "interval_min": 30,
        "mission": "Run daily operations: make sure every pending trip gets a suitable vehicle and driver "
                   "(capacity, availability, license validity), watch trips that are late, and give the "
                   "Worker clear tasks (calls, document follow-ups, POD collection).",
        "tools": ["query", "assign_trip", "create_task", "create_alert"],
    },
    "worker": {
        "name": "AI Worker", "interval_min": 30,
        "mission": "Complete the open tasks assigned to you. For each task do what you can with your tools "
                   "(draft the customer/driver message, look up data, raise an alert) and close it with a "
                   "clear result. If a human must do something physical, say exactly what in the result.",
        "tools": ["query", "send_message", "create_alert"],
    },
    "hr": {
        "name": "AI HR Manager", "interval_min": 1440,
        "mission": "Look after staff: license expiries, staff on leave vs. trips planned, driver ratings, "
                   "monthly payroll summary, hiring needs. Draft job posts when the fleet needs more drivers.",
        "tools": ["query", "create_alert", "create_task", "write_report", "draft_content"],
    },
    "accountant": {
        "name": "AI Accountant", "interval_min": 120,
        "mission": "Keep money flowing: invoice every delivered trip, chase overdue invoices with polite "
                   "reminders, check trip profitability (freight vs fuel/toll/allowance) and flag loss-making "
                   "routes or unusual expenses. Write a short finance summary.",
        "tools": ["query", "kpis", "create_invoice", "send_message", "create_alert", "write_report"],
    },
    "marketing_manager": {
        "name": "AI Marketing Manager", "interval_min": 720,
        "mission": "Grow the business: score and follow up leads, plan campaigns based on our routes and "
                   "services, and brief the Content Creator, Designer and Social Media Manager with tasks.",
        "tools": ["query", "update_lead", "send_message", "create_task", "write_report"],
    },
    "content_creator": {
        "name": "AI Content Creator", "interval_min": 1440,
        "mission": "Write useful, honest content for our customers: social posts, short blogs, ads and email "
                   "campaigns about our services, routes, safety and reliability. Complete content tasks first.",
        "tools": ["query", "draft_content"],
    },
    "designer": {
        "name": "AI Designer", "interval_min": 1440,
        "mission": "Create clean branded graphics as SVG (1080x1080 social posts, banners, flyers) for content "
                   "that has no design yet, using the company name and a consistent colour palette.",
        "tools": ["query", "save_design"],
    },
    "social_media_manager": {
        "name": "AI Social Media Manager", "interval_min": 360,
        "mission": "Keep a steady posting calendar (about one post per platform every 2-3 days). Pick drafts, "
                   "polish them for each platform, attach designs and schedule them. Ask the Content Creator "
                   "for more when the queue is short.",
        "tools": ["query", "schedule_content", "draft_content", "create_task"],
    },
    "website_designer": {
        "name": "AI Website Designer", "interval_min": 10080,
        "mission": "Maintain the company website (served at /site): hero, services, fleet, why-us, contact "
                   "and a lead form note. Keep it accurate to the real company profile and fleet. "
                   "Write semantic HTML fragments with inline styles only, no scripts.",
        "tools": ["query", "update_website"],
    },
}

READABLE = list(db.TABLES) + ["company"]


# ----------------------------------------------------------------------------- helpers
def _alert(level: str, category: str, message: str, key: str, by: str) -> bool:
    if db.q1("SELECT id FROM alerts WHERE dedupe_key = ?", (key,)):
        return False
    db.insert("alerts", {"level": level, "category": category, "message": message, "dedupe_key": key, "resolved": 0, "created_by": by})
    return True


def _outbox(channel: str, recipient: str, subject: str, body: str, by: str, ref: str) -> int | None:
    if ref and db.q1("SELECT id FROM outbox WHERE ref = ?", (ref,)):
        return None
    auto = db.company().get("auto_send_messages") if channel in ("email", "sms", "whatsapp") else db.company().get("auto_publish")
    return db.insert("outbox", {"channel": channel, "recipient": recipient, "subject": subject, "body": body,
                                "status": "approved" if auto else "pending_approval", "created_by": by, "ref": ref})


def _days_until(d: str | None) -> int | None:
    if not d:
        return None
    try:
        return (datetime.fromisoformat(d[:10]).date() - datetime.now().date()).days
    except ValueError:
        return None


def make_invoice(trip_id: int, by: str) -> dict:
    trip = db.get("trips", trip_id)
    if not trip:
        raise ValueError(f"trip {trip_id} not found")
    if trip["status"] != "delivered":
        raise ValueError(f"trip {trip_id} is '{trip['status']}', only delivered trips can be invoiced")
    existing = db.q1("SELECT * FROM invoices WHERE trip_id = ? AND status != 'cancelled'", (trip_id,))
    if existing:
        return existing
    cust = db.get("customers", trip["customer_id"]) or {}
    rate = float(db.company().get("gst_rate") or 0)
    amount = float(trip["freight_amount"] or 0)
    tax = round(amount * rate / 100, 2)
    n = (db.q1("SELECT COUNT(*) n FROM invoices")["n"] or 0) + 1
    inv_id = db.insert("invoices", {
        "number": f"INV-{n:04d}", "customer_id": trip["customer_id"], "trip_id": trip_id, "amount": amount,
        "tax": tax, "total": round(amount + tax, 2), "issue_date": db.today(),
        "due_date": db.days_from_today(int(cust.get("credit_days") or 15)), "status": "unpaid",
    })
    return db.get("invoices", inv_id)


def kpis() -> dict:
    month = datetime.now().strftime("%Y-%m")
    one = lambda sql, p=(): (db.q1(sql, p) or {}).get("v") or 0  # noqa: E731
    return {
        "revenue_this_month": one("SELECT SUM(freight_amount) v FROM trips WHERE status='delivered' AND substr(delivered_at,1,7)=?", (month,)),
        "expenses_this_month": one("SELECT SUM(amount) v FROM expenses WHERE substr(date,1,7)=?", (month,)),
        "receivables_outstanding": one("SELECT SUM(total) v FROM invoices WHERE status IN ('unpaid','overdue')"),
        "overdue_amount": one("SELECT SUM(total) v FROM invoices WHERE status='overdue'"),
        "trips_pending": one("SELECT COUNT(*) v FROM trips WHERE status='pending'"),
        "trips_active": one("SELECT COUNT(*) v FROM trips WHERE status IN ('assigned','in_transit')"),
        "trips_delivered_this_month": one("SELECT COUNT(*) v FROM trips WHERE status='delivered' AND substr(delivered_at,1,7)=?", (month,)),
        "vehicles_total": one("SELECT COUNT(*) v FROM vehicles WHERE status!='inactive'"),
        "vehicles_available": one("SELECT COUNT(*) v FROM vehicles WHERE status='available'"),
        "drivers_active": one("SELECT COUNT(*) v FROM employees WHERE role='driver' AND status='active'"),
        "open_alerts": one("SELECT COUNT(*) v FROM alerts WHERE resolved=0"),
        "open_tasks": one("SELECT COUNT(*) v FROM tasks WHERE status IN ('open','in_progress')"),
        "new_leads": one("SELECT COUNT(*) v FROM leads WHERE status='new'"),
        "pending_approvals": one("SELECT COUNT(*) v FROM outbox WHERE status='pending_approval'"),
    }


def assign(trip_id: int, vehicle_id: int, driver_id: int) -> dict:
    trip, veh, drv = db.get("trips", trip_id), db.get("vehicles", vehicle_id), db.get("employees", driver_id)
    if not trip or trip["status"] != "pending":
        raise ValueError("trip must exist and be pending")
    if not veh or veh["status"] != "available":
        raise ValueError("vehicle must exist and be available")
    if trip["weight_tons"] and veh["capacity_tons"] and float(trip["weight_tons"]) > float(veh["capacity_tons"]):
        raise ValueError("load is heavier than vehicle capacity")
    if not drv or drv["role"] != "driver" or drv["status"] != "active":
        raise ValueError("driver must be an active driver")
    if (_days_until(drv["license_expiry"]) or 1) < 0:
        raise ValueError("driver's license has expired")
    busy = db.q1("SELECT id FROM trips WHERE driver_id=? AND status IN ('assigned','in_transit')", (driver_id,))
    if busy:
        raise ValueError(f"driver is already on trip {busy['id']}")
    db.update("trips", trip_id, {"vehicle_id": vehicle_id, "driver_id": driver_id, "status": "assigned"})
    db.update("vehicles", vehicle_id, {"status": "on_trip"})
    return db.get("trips", trip_id)


# ----------------------------------------------------------------------------- rules (always on)
def rules_general_manager(by):
    k = kpis()
    body = "\n".join(f"- {key.replace('_', ' ').title()}: {val:,.0f}" if isinstance(val, (int, float)) else f"- {key}: {val}" for key, val in k.items())
    db.insert("reports", {"title": f"Daily KPI snapshot {db.today()}", "body": body, "created_by": by})
    return ["KPI snapshot saved"]


def rules_team_leader(by):
    acts = []
    for t in db.q("SELECT * FROM trips WHERE status='pending' ORDER BY pickup_date"):
        vehicles = db.q("SELECT * FROM vehicles WHERE status='available' AND (capacity_tons IS NULL OR capacity_tons >= ?) ORDER BY capacity_tons", (t["weight_tons"] or 0,))
        drivers = db.q("""SELECT * FROM employees e WHERE role='driver' AND status='active'
                          AND (license_expiry IS NULL OR license_expiry >= date('now'))
                          AND NOT EXISTS (SELECT 1 FROM trips x WHERE x.driver_id=e.id AND x.status IN ('assigned','in_transit'))
                          ORDER BY rating DESC""")
        if vehicles and drivers:
            assign(t["id"], vehicles[0]["id"], drivers[0]["id"])
            acts.append(f"Trip {t['id']} ({t['origin']}->{t['destination']}) assigned {vehicles[0]['reg_no']} / {drivers[0]['name']}")
        elif _alert("warning", "dispatch", f"Trip {t['id']} {t['origin']}->{t['destination']} has no free vehicle/driver", f"nodispatch:{t['id']}:{db.today()}", by):
            acts.append(f"No resources for trip {t['id']} - alert raised")
    for t in db.q("SELECT * FROM trips WHERE status IN ('assigned','in_transit') AND pickup_date < date('now','-3 day')"):
        if _alert("warning", "operations", f"Trip {t['id']} picked up {t['pickup_date']} still not delivered - check status", f"late:{t['id']}", by):
            db.insert("tasks", {"title": f"Call driver for trip {t['id']} status", "details": f"Trip {t['origin']}->{t['destination']} is late. Get location/ETA and update the trip.", "assigned_role": "worker", "created_by": by, "priority": "high", "status": "open"})
            acts.append(f"Late trip {t['id']} flagged")
    interval = int(db.company().get("service_interval_km") or 10000)
    for v in db.q("SELECT * FROM vehicles WHERE status!='inactive'"):
        if v["odometer_km"] and v["last_service_km"] is not None and v["odometer_km"] - v["last_service_km"] >= interval:
            if _alert("warning", "maintenance", f"{v['reg_no']} is due for service ({v['odometer_km'] - v['last_service_km']} km since last)", f"service:{v['id']}:{v['last_service_km']}", by):
                acts.append(f"Service due: {v['reg_no']}")
        for field in ("insurance_expiry", "permit_expiry", "fitness_expiry"):
            d = _days_until(v[field])
            if d is not None and d <= 30:
                lvl = "critical" if d < 0 else "warning"
                if _alert(lvl, "compliance", f"{v['reg_no']} {field.replace('_', ' ')} {'expired' if d < 0 else f'in {d} days'} ({v[field]})", f"{field}:{v['id']}:{v[field]}", by):
                    acts.append(f"{v['reg_no']} {field} alert")
    return acts


def rules_hr(by):
    acts = []
    for e in db.q("SELECT * FROM employees WHERE status='active' AND license_expiry IS NOT NULL"):
        d = _days_until(e["license_expiry"])
        if d is not None and d <= 30:
            if _alert("critical" if d < 0 else "warning", "hr", f"{e['name']}'s driving license {'EXPIRED' if d < 0 else f'expires in {d} days'} ({e['license_expiry']})", f"license:{e['id']}:{e['license_expiry']}", by):
                acts.append(f"License alert: {e['name']}")
    month = datetime.now().strftime("%Y-%m")
    if not db.q1("SELECT id FROM reports WHERE title = ?", (f"Payroll summary {month}",)):
        staff = db.q("SELECT name, role, monthly_salary FROM employees WHERE status='active' ORDER BY role, name")
        total = sum(float(s["monthly_salary"] or 0) for s in staff)
        lines = [f"- {s['name']} ({s['role']}): {float(s['monthly_salary'] or 0):,.0f}" for s in staff]
        db.insert("reports", {"title": f"Payroll summary {month}", "body": "\n".join(lines) + f"\n\nTotal: {total:,.0f}", "created_by": by})
        acts.append("Payroll summary created")
    drivers = (db.q1("SELECT COUNT(*) n FROM employees WHERE role='driver' AND status='active'") or {})["n"]
    vehicles = (db.q1("SELECT COUNT(*) n FROM vehicles WHERE status!='inactive'") or {})["n"]
    if vehicles and drivers < vehicles and _alert("info", "hr", f"Only {drivers} active drivers for {vehicles} vehicles - consider hiring", f"hiring:{month}:{drivers}:{vehicles}", by):
        acts.append("Hiring need flagged")
    return acts


def rules_accountant(by):
    acts = []
    for t in db.q("SELECT t.id FROM trips t WHERE t.status='delivered' AND NOT EXISTS (SELECT 1 FROM invoices i WHERE i.trip_id=t.id AND i.status!='cancelled')"):
        inv = make_invoice(t["id"], by)
        acts.append(f"Invoice {inv['number']} created for trip {t['id']} ({inv['total']:,.0f})")
    for inv in db.q("SELECT * FROM invoices WHERE status='unpaid' AND due_date < date('now')"):
        db.update("invoices", inv["id"], {"status": "overdue"})
        acts.append(f"Invoice {inv['number']} marked overdue")
    co = db.company()
    for inv in db.q("SELECT i.*, c.name cname, c.email cemail, c.phone cphone FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.status='overdue'"):
        week = datetime.now().strftime("%Y-W%W")
        body = (f"Dear {inv['cname']},\n\nThis is a friendly reminder that invoice {inv['number']} for Rs. {inv['total']:,.2f} "
                f"was due on {inv['due_date']}. Kindly arrange the payment at the earliest. Please ignore this message if already paid.\n\n"
                f"Thank you,\nAccounts, {co.get('name')}\n{co.get('phone')}")
        if _outbox("email" if inv["cemail"] else "whatsapp", inv["cemail"] or inv["cphone"] or "", f"Payment reminder: {inv['number']}", body, by, f"reminder:{inv['id']}:{week}"):
            acts.append(f"Reminder drafted for {inv['number']}")
    return acts


def rules_social_media_manager(by):
    acts = []
    for c in db.q("SELECT * FROM content WHERE status='approved' AND (scheduled_for IS NULL OR scheduled_for <= ?)", (db.now(),)):
        text = (c["body"] or "") + (("\n\n" + c["hashtags"]) if c["hashtags"] else "")
        if c["design_id"]:
            text += f"\n\n[image: design #{c['design_id']} - {_base_url()}/api/designs/{c['design_id']}.svg]"
        _outbox(c["platform"] or "facebook", c["platform"] or "", c["title"] or "", text, by, f"content:{c['id']}")
        db.q("UPDATE outbox SET status='approved' WHERE ref=? AND status='pending_approval'", (f"content:{c['id']}",))  # content already approved
        db.update("content", c["id"], {"status": "published", "published_at": db.now()})
        acts.append(f"Content {c['id']} sent to {c['platform']}")
    return acts


def rules_website_designer(by):
    if db.q1("SELECT id FROM website_sections"):
        return []
    co = db.company()
    services = "".join(f"<li>{s.strip()}</li>" for s in (co.get("services") or "").split(",") if s.strip())
    sections = [
        ("hero", "Home", f"<h1>{co.get('name')}</h1><p style='font-size:1.25rem'>{co.get('tagline')}</p><p><a href='#contact'>Get a quote</a></p>"),
        ("about", "About us", f"<h2>About us</h2><p>{co.get('about')}</p>"),
        ("services", "Services", f"<h2>Our services</h2><ul>{services}</ul>"),
        ("contact", "Contact", f"<h2 id='contact'>Contact</h2><p>{co.get('address')}, {co.get('city')}<br>Phone: {co.get('phone')}<br>Email: {co.get('email')}</p>"),
    ]
    for i, (key, title, html) in enumerate(sections):
        db.insert("website_sections", {"key": key, "title": title, "position": i, "html": html, "updated_by": by})
    return ["Starter website created"]


def rules_worker(by):
    return []


def rules_marketing_manager(by):
    acts = []
    for lead in db.q("SELECT * FROM leads WHERE status='new' AND score IS NULL"):
        score = 40 + (20 if lead["email"] else 0) + (20 if lead["phone"] else 0) + (20 if lead["requirement"] and len(lead["requirement"]) > 20 else 0)
        db.update("leads", lead["id"], {"score": score, "next_action": "Call and send a quote"})
        db.insert("tasks", {"title": f"Contact lead: {lead['name']} ({lead['company'] or ''})", "details": f"Requirement: {lead['requirement']}\nPhone: {lead['phone']} Email: {lead['email']}", "assigned_role": "worker", "created_by": by, "priority": "high" if score >= 80 else "normal", "status": "open"})
        acts.append(f"Lead {lead['name']} scored {score}, follow-up task created")
    return acts


def rules_content_creator(by):
    return []


def rules_designer(by):
    return []


RULES = {k: globals()[f"rules_{k}"] for k in ROLES}


def _base_url() -> str:
    return f"http://127.0.0.1:{os.environ.get('APP_PORT', '8100')}"


# ----------------------------------------------------------------------------- AI tools
def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


S, I, N = {"type": "string"}, {"type": "integer"}, {"type": "number"}

TOOL_DEFS = {
    "query": {
        "description": "Read ERP records. Tables: " + ", ".join(READABLE) + ". Returns up to `limit` rows (newest first). "
                       "`where` is an optional dict of column -> exact value filters.",
        "input_schema": _obj({"table": {"type": "string", "enum": READABLE}, "where": {"type": "object"}, "limit": I}, ["table"]),
    },
    "kpis": {"description": "Current business KPIs (revenue, expenses, receivables, fleet, trips, alerts).", "input_schema": _obj({}, [])},
    "create_task": {
        "description": "Delegate a task to a team member role. Roles: " + ", ".join(ROLES) + ", or 'human' for something only a person can do.",
        "input_schema": _obj({"assigned_role": S, "title": S, "details": S, "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]}}, ["assigned_role", "title", "details"]),
    },
    "complete_task": {
        "description": "Close one of YOUR tasks with a result (what was done / what a human must still do).",
        "input_schema": _obj({"task_id": I, "result": S, "status": {"type": "string", "enum": ["done", "cancelled", "in_progress"]}}, ["task_id", "result"]),
    },
    "write_report": {"description": "Save a report (markdown) for management.", "input_schema": _obj({"title": S, "body": S}, ["title", "body"])},
    "create_alert": {
        "description": "Raise an alert on the dashboard.",
        "input_schema": _obj({"level": {"type": "string", "enum": ["info", "warning", "critical"]}, "category": S, "message": S}, ["level", "message"]),
    },
    "assign_trip": {"description": "Assign an available vehicle and active driver to a pending trip. Validates capacity, availability and license.", "input_schema": _obj({"trip_id": I, "vehicle_id": I, "driver_id": I}, ["trip_id", "vehicle_id", "driver_id"])},
    "create_invoice": {"description": "Create the invoice for a delivered trip (GST applied from company settings).", "input_schema": _obj({"trip_id": I}, ["trip_id"])},
    "send_message": {
        "description": "Queue an email / SMS / WhatsApp message to a customer, lead, driver or staff member. Goes to the Outbox; sent after approval or automatically if auto-send is on.",
        "input_schema": _obj({"channel": {"type": "string", "enum": ["email", "sms", "whatsapp"]}, "recipient": S, "subject": S, "body": S}, ["channel", "recipient", "body"]),
    },
    "update_lead": {
        "description": "Update a sales lead's status, score (0-100) or next action.",
        "input_schema": _obj({"lead_id": I, "status": {"type": "string", "enum": ["new", "contacted", "quoted", "won", "lost"]}, "score": I, "next_action": S}, ["lead_id"]),
    },
    "draft_content": {
        "description": "Create a content draft (social post, blog, ad, email campaign or job post).",
        "input_schema": _obj({
            "kind": {"type": "string", "enum": ["social_post", "blog", "ad", "email_campaign", "job_post"]},
            "platform": {"type": "string", "enum": ["facebook", "instagram", "linkedin", "x", "whatsapp", "youtube", "website", "email"]},
            "title": S, "body": S, "hashtags": S}, ["kind", "platform", "title", "body"]),
    },
    "schedule_content": {
        "description": "Polish and schedule a content item: optionally rewrite body/hashtags, attach a design, set publish time "
                       "(ISO 'YYYY-MM-DD HH:MM:SS'). It is then submitted for approval (or auto-approved if auto-publish is on).",
        "input_schema": _obj({"content_id": I, "scheduled_for": S, "body": S, "hashtags": S, "design_id": I}, ["content_id", "scheduled_for"]),
    },
    "save_design": {
        "description": "Save an SVG graphic (complete <svg> document with viewBox; no scripts, no external images). Optionally attach to a content item.",
        "input_schema": _obj({"name": S, "kind": {"type": "string", "enum": ["social_post", "banner", "logo", "flyer", "other"]}, "svg": S, "content_id": I}, ["name", "kind", "svg"]),
    },
    "update_website": {
        "description": "Create or replace a website section (served at /site). key e.g. hero, about, services, fleet, why_us, testimonials, contact. "
                       "html is a fragment with inline styles; scripts are removed.",
        "input_schema": _obj({"key": S, "title": S, "position": I, "html": S}, ["key", "title", "html"]),
    },
}


def sanitize_html(html: str) -> str:
    html = re.sub(r"(?is)<\s*(script|iframe|object|embed|foreignObject)\b.*?(</\s*\1\s*>|$)", "", html)
    html = re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", html)
    html = re.sub(r"(?i)(href|src|xlink:href)\s*=\s*([\"'])\s*javascript:[^\"']*\2", r'\1="#"', html)
    return html


def run_tool(role: str, name: str, a: dict) -> object:
    by = role
    if name == "query":
        table = a["table"]
        if table not in READABLE:
            raise ValueError("unknown table")
        if table == "company":
            return db.company()
        where = a.get("where") or {}
        cols = db.TABLES[table]["cols"]
        bad = [k for k in where if k not in cols and k != "id"]
        if bad:
            raise ValueError(f"unknown columns {bad}; columns are {['id', *cols]}")
        sql = f"SELECT * FROM {table}" + (" WHERE " + " AND ".join(f"{k} = ?" for k in where) if where else "")
        rows = db.q(sql + " ORDER BY id DESC LIMIT ?", [*where.values(), min(int(a.get("limit") or 25), 100)])
        for r in rows:
            for k in ("svg", "html"):
                if r.get(k):
                    r[k] = r[k][:200] + "...(truncated)"
        return rows
    if name == "kpis":
        return kpis()
    if name == "create_task":
        tid = db.insert("tasks", {**a, "priority": a.get("priority") or "normal", "status": "open", "created_by": by})
        return {"task_id": tid}
    if name == "complete_task":
        t = db.get("tasks", a["task_id"])
        if not t or t["assigned_role"] != role:
            raise ValueError("that task is not assigned to you")
        db.update("tasks", a["task_id"], {"status": a.get("status") or "done", "result": a["result"]})
        return "ok"
    if name == "write_report":
        return {"report_id": db.insert("reports", {**a, "created_by": by})}
    if name == "create_alert":
        key = f"ai:{role}:{a['message'][:120]}"
        return "created" if _alert(a["level"], a.get("category") or role, a["message"], key, by) else "already exists"
    if name == "assign_trip":
        return assign(a["trip_id"], a["vehicle_id"], a["driver_id"])
    if name == "create_invoice":
        return make_invoice(a["trip_id"], by)
    if name == "send_message":
        oid = _outbox(a["channel"], a["recipient"], a.get("subject") or "", a["body"], by, "")
        return {"outbox_id": oid, "note": "queued"}
    if name == "update_lead":
        if not db.get("leads", a["lead_id"]):
            raise ValueError("lead not found")
        db.update("leads", a.pop("lead_id"), a)
        return "ok"
    if name == "draft_content":
        return {"content_id": db.insert("content", {**a, "status": "draft", "created_by": by})}
    if name == "schedule_content":
        c = db.get("content", a["content_id"])
        if not c:
            raise ValueError("content not found")
        datetime.fromisoformat(a["scheduled_for"])
        auto = db.company().get("auto_publish")
        data = {k: v for k, v in a.items() if k in ("scheduled_for", "body", "hashtags", "design_id") and v}
        data["status"] = "approved" if auto else "pending_approval"
        db.update("content", c["id"], data)
        return {"status": data["status"]}
    if name == "save_design":
        svg = sanitize_html(a["svg"])
        if "<svg" not in svg:
            raise ValueError("svg must contain an <svg> element")
        did = db.insert("designs", {"name": a["name"], "kind": a["kind"], "svg": svg, "created_by": by})
        if a.get("content_id") and db.get("content", a["content_id"]):
            db.update("content", a["content_id"], {"design_id": did})
        return {"design_id": did}
    if name == "update_website":
        html = sanitize_html(a["html"])
        row = db.q1("SELECT id FROM website_sections WHERE key = ?", (a["key"],))
        data = {"key": a["key"], "title": a["title"], "html": html, "updated_by": by}
        if a.get("position") is not None:
            data["position"] = a["position"]
        if row:
            db.update("website_sections", row["id"], data)
        else:
            data.setdefault("position", 50)
            db.insert("website_sections", data)
        return "saved"
    raise ValueError(f"unknown tool {name}")


def tools_for(role: str) -> list[dict]:
    names = list(dict.fromkeys(ROLES[role]["tools"] + ["complete_task", "create_task"]))
    return [{"name": n, **TOOL_DEFS[n]} for n in names]


def ai_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def system_prompt(role: str) -> str:
    co = db.company()
    r = ROLES[role]
    team = "\n".join(f"- {k}: {v['name']}" for k, v in ROLES.items())
    return (
        f"You are the {r['name']} of {co.get('name')}, a road transport / logistics company in {co.get('city')}, India. "
        f"You work inside the company's ERP together with an AI team and the human owner.\n\n"
        f"Company profile: {json.dumps({k: co.get(k) for k in ('name', 'tagline', 'services', 'about', 'phone', 'email', 'address', 'city')})}\n\n"
        f"Your standing mission: {r['mission']}\n\nThe team (use these keys with create_task):\n{team}\n- human: the owner/staff\n\n"
        "How to work:\n"
        "- Base every decision on data you read with your tools. Never invent customers, numbers, trips or results.\n"
        "- First handle the open tasks assigned to you, closing each with complete_task. Then pursue your mission.\n"
        "- Avoid duplicates: check existing tasks, content, alerts and reports before creating new ones.\n"
        "- Be economical: do the few most valuable actions, not everything at once.\n"
        "- Messages to customers must be polite, accurate and in simple English (add a short Tamil/Hindi line only if the customer data suggests it).\n"
        "- Money, hiring/firing, legal and safety decisions are recommendations for the human owner: create a task for 'human'.\n"
        "- Finish with a 2-5 line summary of what you did and what needs human attention."
    )


def run_ai(role: str, rule_actions: list[str]) -> tuple[str, list[str], int, int]:
    import anthropic

    client = anthropic.Anthropic()
    my_tasks = db.q("SELECT id, title, details, priority, created_by FROM tasks WHERE assigned_role=? AND status IN ('open','in_progress') ORDER BY id LIMIT 15", (role,))
    content = (
        f"Now: {db.now()}. Your scheduled run has started.\n"
        f"Automatic rules already did this run: {json.dumps(rule_actions) if rule_actions else 'nothing'}\n"
        f"Your open tasks: {json.dumps(my_tasks, default=str) if my_tasks else 'none'}\n"
        f"KPIs: {json.dumps(kpis())}"
    )
    messages: list = [{"role": "user", "content": content}]
    actions, tin, tout, summary = [], 0, 0, ""
    tools = tools_for(role)
    system = [{"type": "text", "text": system_prompt(role), "cache_control": {"type": "ephemeral"}}]
    for _ in range(MAX_STEPS):
        resp = client.beta.messages.create(
            model=MODEL, max_tokens=16000, system=system, tools=tools, messages=messages,
            output_config={"effort": EFFORT},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        tin += resp.usage.input_tokens or 0
        tout += resp.usage.output_tokens or 0
        if resp.stop_reason == "refusal":
            summary = "The model declined this run."
            break
        messages.append({"role": "assistant", "content": resp.content})
        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        if text:
            summary = text
        calls = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not calls:
            break
        results = []
        for call in calls:
            try:
                out = run_tool(role, call.name, dict(call.input or {}))
                actions.append(f"{call.name}: {json.dumps(call.input, default=str)[:300]}")
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(out, default=str)[:20000]})
            except Exception as e:  # tool errors go back to the model so it can correct itself
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": f"Error: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})
    else:
        summary = (summary + "\n(stopped: step limit reached)").strip()
    return summary, actions, tin, tout


# ----------------------------------------------------------------------------- runner
def ensure_agents() -> None:
    for key, r in ROLES.items():
        if not db.q1("SELECT key FROM agents WHERE key=?", (key,)):
            db.execute("INSERT INTO agents (key, name, enabled, interval_min, mission, next_run) VALUES (?,?,?,?,?,?)",
                       (key, r["name"], 1, r["interval_min"], r["mission"], db.now()))


def run_agent(role: str, use_ai: bool | None = None) -> dict:
    if role not in ROLES:
        raise ValueError("unknown agent")
    agent = db.q1("SELECT * FROM agents WHERE key=?", (role,)) or {}
    if agent.get("mission"):
        ROLES[role]["mission"] = agent["mission"]
    started = db.now()
    run_id = db.execute("INSERT INTO agent_runs (agent_key, started_at, status) VALUES (?,?, 'running')", (role, started))
    mode, status, summary, actions, tin, tout = "rules", "ok", "", [], 0, 0
    try:
        actions = RULES[role](role)
        summary = "; ".join(actions) or "Nothing to do."
        if (ai_available() if use_ai is None else use_ai):
            mode = "ai"
            summary, ai_actions, tin, tout = run_ai(role, actions)
            actions += ai_actions
    except Exception as e:
        status, summary = "error", f"{type(e).__name__}: {e}"
        traceback.print_exc()
    interval = int(agent.get("interval_min") or ROLES[role]["interval_min"])
    db.execute("UPDATE agents SET last_run=?, next_run=? WHERE key=?",
               (started, (datetime.now() + timedelta(minutes=interval)).replace(microsecond=0).isoformat(sep=" "), role))
    db.execute("UPDATE agent_runs SET finished_at=?, mode=?, status=?, summary=?, actions=?, tokens_in=?, tokens_out=? WHERE id=?",
               (db.now(), mode, status, summary, json.dumps(actions), tin, tout, run_id))
    return db.q1("SELECT * FROM agent_runs WHERE id=?", (run_id,))
