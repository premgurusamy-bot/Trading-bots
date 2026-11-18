"""SQLite storage for the Transport ERP: schema, table metadata and small helpers."""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, date, timedelta
from pathlib import Path

DB_PATH = Path(os.environ.get("ERP_DB_PATH", Path(__file__).resolve().parent.parent / "data" / "erp.db"))
_lock = threading.RLock()

# Column spec: name -> (type, options). type in text|int|real|date|datetime|select|longtext|ref:<table>
TABLES: dict[str, dict] = {
    "customers": {
        "label": "Customers",
        "cols": {
            "name": ("text", None), "contact_person": ("text", None), "phone": ("text", None),
            "email": ("text", None), "city": ("text", None), "gst_no": ("text", None),
            "credit_days": ("int", None),
        },
    },
    "vehicles": {
        "label": "Fleet",
        "cols": {
            "reg_no": ("text", None),
            "type": ("select", ["Truck", "Trailer", "Container", "Tanker", "Mini truck", "Tempo", "Van"]),
            "capacity_tons": ("real", None),
            "status": ("select", ["available", "on_trip", "maintenance", "inactive"]),
            "odometer_km": ("int", None), "last_service_km": ("int", None),
            "insurance_expiry": ("date", None), "permit_expiry": ("date", None), "fitness_expiry": ("date", None),
        },
    },
    "employees": {
        "label": "Staff & Drivers",
        "cols": {
            "name": ("text", None),
            "role": ("select", ["driver", "helper", "dispatcher", "accountant", "mechanic", "sales", "manager", "office"]),
            "phone": ("text", None), "monthly_salary": ("real", None), "join_date": ("date", None),
            "license_no": ("text", None), "license_expiry": ("date", None),
            "status": ("select", ["active", "on_leave", "left"]), "rating": ("real", None),
        },
    },
    "trips": {
        "label": "Trips / Bookings",
        "cols": {
            "customer_id": ("ref:customers", None), "origin": ("text", None), "destination": ("text", None),
            "goods": ("text", None), "weight_tons": ("real", None), "distance_km": ("int", None),
            "pickup_date": ("date", None), "freight_amount": ("real", None),
            "vehicle_id": ("ref:vehicles", None), "driver_id": ("ref:employees", None),
            "status": ("select", ["pending", "assigned", "in_transit", "delivered", "cancelled"]),
            "delivered_at": ("date", None),
        },
    },
    "expenses": {
        "label": "Expenses",
        "cols": {
            "date": ("date", None),
            "category": ("select", ["fuel", "toll", "maintenance", "salary", "driver_allowance", "office", "insurance", "other"]),
            "amount": ("real", None), "vehicle_id": ("ref:vehicles", None), "trip_id": ("ref:trips", None),
            "note": ("text", None),
        },
    },
    "invoices": {
        "label": "Invoices",
        "cols": {
            "number": ("text", None), "customer_id": ("ref:customers", None), "trip_id": ("ref:trips", None),
            "amount": ("real", None), "tax": ("real", None), "total": ("real", None),
            "issue_date": ("date", None), "due_date": ("date", None),
            "status": ("select", ["unpaid", "overdue", "paid", "cancelled"]), "paid_date": ("date", None),
        },
    },
    "leads": {
        "label": "Leads (Sales)",
        "cols": {
            "name": ("text", None), "company": ("text", None), "phone": ("text", None), "email": ("text", None),
            "source": ("select", ["website", "phone", "referral", "social", "walk_in", "other"]),
            "requirement": ("longtext", None),
            "status": ("select", ["new", "contacted", "quoted", "won", "lost"]),
            "score": ("int", None), "next_action": ("text", None),
        },
    },
    "content": {
        "label": "Content & Social",
        "cols": {
            "kind": ("select", ["social_post", "blog", "ad", "email_campaign", "job_post"]),
            "platform": ("select", ["facebook", "instagram", "linkedin", "x", "whatsapp", "youtube", "website", "email"]),
            "title": ("text", None), "body": ("longtext", None), "hashtags": ("text", None),
            "design_id": ("ref:designs", None),
            "status": ("select", ["draft", "pending_approval", "approved", "published", "rejected"]),
            "scheduled_for": ("datetime", None), "created_by": ("text", None), "published_at": ("datetime", None),
        },
    },
    "designs": {
        "label": "Designs",
        "cols": {
            "name": ("text", None), "kind": ("select", ["social_post", "banner", "logo", "flyer", "other"]),
            "svg": ("longtext", None), "created_by": ("text", None),
        },
    },
    "website_sections": {
        "label": "Website",
        "cols": {
            "key": ("text", None), "title": ("text", None), "position": ("int", None),
            "html": ("longtext", None), "updated_by": ("text", None),
        },
    },
    "tasks": {
        "label": "Tasks",
        "cols": {
            "title": ("text", None), "details": ("longtext", None), "assigned_role": ("text", None),
            "created_by": ("text", None), "priority": ("select", ["low", "normal", "high", "urgent"]),
            "status": ("select", ["open", "in_progress", "done", "cancelled"]), "result": ("longtext", None),
        },
    },
    "alerts": {
        "label": "Alerts",
        "cols": {
            "level": ("select", ["info", "warning", "critical"]), "category": ("text", None),
            "message": ("text", None), "dedupe_key": ("text", None), "resolved": ("int", None),
            "created_by": ("text", None),
        },
    },
    "reports": {
        "label": "Reports",
        "cols": {"title": ("text", None), "body": ("longtext", None), "created_by": ("text", None)},
    },
    "outbox": {
        "label": "Outbox / Approvals",
        "cols": {
            "channel": ("select", ["email", "sms", "whatsapp", "facebook", "instagram", "linkedin", "x", "youtube", "website"]),
            "recipient": ("text", None), "subject": ("text", None), "body": ("longtext", None),
            "status": ("select", ["pending_approval", "approved", "sent", "failed", "rejected"]),
            "created_by": ("text", None), "ref": ("text", None), "error": ("text", None), "sent_at": ("datetime", None),
        },
    },
}

SQL_TYPES = {"int": "INTEGER", "real": "REAL"}

EXTRA_SQL = """
CREATE TABLE IF NOT EXISTS company (
  id INTEGER PRIMARY KEY CHECK (id = 1), name TEXT, tagline TEXT, phone TEXT, email TEXT,
  address TEXT, city TEXT, services TEXT, about TEXT, gst_rate REAL DEFAULT 18,
  auto_publish INTEGER DEFAULT 0, auto_send_messages INTEGER DEFAULT 0, service_interval_km INTEGER DEFAULT 10000
);
CREATE TABLE IF NOT EXISTS agents (
  key TEXT PRIMARY KEY, name TEXT, enabled INTEGER DEFAULT 1, interval_min INTEGER,
  mission TEXT, last_run TEXT, next_run TEXT
);
CREATE TABLE IF NOT EXISTS agent_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, agent_key TEXT, started_at TEXT, finished_at TEXT,
  mode TEXT, status TEXT, summary TEXT, actions TEXT, tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_dedupe ON alerts(dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_site_key ON website_sections(key);
"""


def now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def today() -> str:
    return date.today().isoformat()


def days_from_today(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


_con: sqlite3.Connection | None = None


def con() -> sqlite3.Connection:
    global _con
    if _con is None:
        _con = connect()
    return _con


def reset_connection() -> None:
    global _con
    with _lock:
        if _con is not None:
            _con.close()
        _con = None


def q(sql: str, params: tuple | list = ()) -> list[dict]:
    with _lock:
        cur = con().execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        con().commit()
        return rows


def q1(sql: str, params: tuple | list = ()) -> dict | None:
    rows = q(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple | list = ()) -> int:
    with _lock:
        cur = con().execute(sql, params)
        con().commit()
        return cur.lastrowid


def clean(table: str, data: dict) -> dict:
    cols = TABLES[table]["cols"]
    return {k: v for k, v in data.items() if k in cols}


def insert(table: str, data: dict) -> int:
    data = clean(table, data)
    keys = list(data)
    sql = f"INSERT INTO {table} ({', '.join(keys + ['created_at', 'updated_at'])}) VALUES ({', '.join(['?'] * (len(keys) + 2))})"
    return execute(sql, [data[k] for k in keys] + [now(), now()])


def update(table: str, row_id: int, data: dict) -> None:
    data = clean(table, data)
    if not data:
        return
    sets = ", ".join(f"{k} = ?" for k in data)
    execute(f"UPDATE {table} SET {sets}, updated_at = ? WHERE id = ?", list(data.values()) + [now(), row_id])


def get(table: str, row_id: int) -> dict | None:
    return q1(f"SELECT * FROM {table} WHERE id = ?", (row_id,))


def init_db() -> None:
    with _lock:
        c = con()
        for name, spec in TABLES.items():
            cols = ", ".join(f"{col} {SQL_TYPES.get(t, 'TEXT')}" for col, (t, _) in spec["cols"].items())
            c.execute(f"CREATE TABLE IF NOT EXISTS {name} (id INTEGER PRIMARY KEY AUTOINCREMENT, {cols}, created_at TEXT, updated_at TEXT)")
            existing = {r[1] for r in c.execute(f"PRAGMA table_info({name})")}
            for col, (t, _) in spec["cols"].items():  # forward-compatible column additions
                if col not in existing:
                    c.execute(f"ALTER TABLE {name} ADD COLUMN {col} {SQL_TYPES.get(t, 'TEXT')}")
        c.executescript(EXTRA_SQL)
        if not c.execute("SELECT 1 FROM company").fetchone():
            c.execute(
                "INSERT INTO company (id, name, tagline, phone, email, address, city, services, about) VALUES (1,?,?,?,?,?,?,?,?)",
                ("My Transport Co.", "Safe, on-time cargo across India", "+91 90000 00000", "info@example.com",
                 "Transport Nagar", "Chennai", "Full truck load, Part load, Container transport, Warehousing",
                 "We move goods safely and on time with a modern, GPS-tracked fleet."),
            )
        c.commit()


def company() -> dict:
    return q1("SELECT * FROM company WHERE id = 1") or {}


def seed_demo() -> None:
    """Insert a small demo data set if the database is empty."""
    if q1("SELECT COUNT(*) n FROM customers")["n"]:
        return
    c1 = insert("customers", {"name": "Sri Balaji Textiles", "contact_person": "Ravi", "phone": "9840000001", "email": "ravi@balajitex.example", "city": "Tiruppur", "credit_days": 30})
    c2 = insert("customers", {"name": "Annai Cement Agencies", "contact_person": "Meena", "phone": "9840000002", "email": "accounts@annaicement.example", "city": "Coimbatore", "credit_days": 15})
    v1 = insert("vehicles", {"reg_no": "TN 38 AB 1234", "type": "Truck", "capacity_tons": 16, "status": "available", "odometer_km": 84500, "last_service_km": 76000, "insurance_expiry": days_from_today(20), "permit_expiry": days_from_today(200), "fitness_expiry": days_from_today(90)})
    v2 = insert("vehicles", {"reg_no": "TN 66 CD 5678", "type": "Container", "capacity_tons": 25, "status": "available", "odometer_km": 40200, "last_service_km": 38000, "insurance_expiry": days_from_today(300), "permit_expiry": days_from_today(12), "fitness_expiry": days_from_today(150)})
    d1 = insert("employees", {"name": "Murugan K", "role": "driver", "phone": "9000000011", "monthly_salary": 22000, "join_date": "2022-04-01", "license_no": "TN3820190001", "license_expiry": days_from_today(25), "status": "active", "rating": 4.5})
    insert("employees", {"name": "Selvam R", "role": "driver", "phone": "9000000012", "monthly_salary": 21000, "join_date": "2023-01-10", "license_no": "TN6620200002", "license_expiry": days_from_today(400), "status": "active", "rating": 4.2})
    insert("employees", {"name": "Priya S", "role": "accountant", "phone": "9000000013", "monthly_salary": 28000, "join_date": "2021-07-01", "status": "active"})
    t1 = insert("trips", {"customer_id": c1, "origin": "Tiruppur", "destination": "Bengaluru", "goods": "Garments", "weight_tons": 8, "distance_km": 320, "pickup_date": days_from_today(-6), "freight_amount": 28000, "vehicle_id": v1, "driver_id": d1, "status": "delivered", "delivered_at": days_from_today(-5)})
    insert("trips", {"customer_id": c2, "origin": "Coimbatore", "destination": "Madurai", "goods": "Cement bags", "weight_tons": 20, "distance_km": 215, "pickup_date": today(), "freight_amount": 24000, "status": "pending"})
    insert("trips", {"customer_id": c1, "origin": "Tiruppur", "destination": "Chennai", "goods": "Yarn", "weight_tons": 10, "distance_km": 470, "pickup_date": days_from_today(1), "freight_amount": 36000, "status": "pending"})
    insert("expenses", {"date": days_from_today(-6), "category": "fuel", "amount": 9800, "vehicle_id": v1, "trip_id": t1, "note": "Diesel 105 L"})
    insert("expenses", {"date": days_from_today(-6), "category": "toll", "amount": 1450, "vehicle_id": v1, "trip_id": t1})
    insert("invoices", {"number": "INV-0001", "customer_id": c2, "amount": 20000, "tax": 1000, "total": 21000, "issue_date": days_from_today(-40), "due_date": days_from_today(-25), "status": "unpaid"})
    insert("leads", {"name": "Karthik", "company": "Fresh Farms Pvt Ltd", "phone": "9000000099", "email": "karthik@freshfarms.example", "source": "website", "requirement": "Weekly reefer/part-load from Ooty to Chennai, ~5 tons", "status": "new"})
