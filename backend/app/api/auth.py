"""Local dashboard authentication. A password is set in the first-run wizard (PBKDF2-SHA256, 600k
iterations, stored hashed in the settings table). Login returns a random session token held in
memory. Every /api route except health and auth requires `Authorization: Bearer <token>`.
The Dhan token is NEVER sent to the browser."""
from __future__ import annotations

import hashlib
import hmac
import secrets as pysecrets
import time
from typing import Optional

from fastapi import Depends, HTTPException, Request, status

from app import db as dbm

ITER = 600_000
_sessions: dict[str, float] = {}
_failed: dict[str, list[float]] = {}


def _hash(pw: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER).hex()


def password_is_set(Session) -> bool:
    with Session() as s:
        return s.get(dbm.SettingRow, "auth") is not None


def set_password(Session, pw: str):
    if len(pw) < 8:
        raise ValueError("Password must be at least 8 characters")
    salt = pysecrets.token_bytes(16)
    with Session() as s:
        row = s.get(dbm.SettingRow, "auth")
        if row is None:
            row = dbm.SettingRow(key="auth", value={})
            s.add(row)
        row.value = {"salt": salt.hex(), "hash": _hash(pw, salt), "iter": ITER}
        s.commit()


def verify(Session, pw: str) -> bool:
    with Session() as s:
        row = s.get(dbm.SettingRow, "auth")
    if row is None:
        return False
    return hmac.compare_digest(_hash(pw, bytes.fromhex(row.value["salt"])), row.value["hash"])


def login(Session, pw: str, client: str, ttl_hours: int) -> Optional[str]:
    now = time.time()
    fails = [t for t in _failed.get(client, []) if now - t < 300]
    if len(fails) >= 5:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed logins; wait 5 minutes")
    if not verify(Session, pw):
        _failed[client] = fails + [now]
        return None
    tok = pysecrets.token_urlsafe(32)
    _sessions[tok] = now + ttl_hours * 3600
    return tok


def logout(tok: str):
    _sessions.pop(tok, None)


def valid(tok: Optional[str]) -> bool:
    if not tok:
        return False
    exp = _sessions.get(tok)
    if exp is None or exp < time.time():
        _sessions.pop(tok, None)
        return False
    return True


def require_auth(request: Request) -> str:
    h = request.headers.get("authorization", "")
    tok = h[7:] if h.lower().startswith("bearer ") else None
    if not valid(tok):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login required")
    return tok
