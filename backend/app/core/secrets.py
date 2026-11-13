"""Credential storage. Never logged, never returned to the frontend, never stored in the database.

Primary store: the OS credential vault through `keyring` (Windows Credential Manager on Windows).
Fallback: environment variables DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN (e.g. from the git-ignored .env).
"""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("secrets")
SERVICE = "nifty-banknifty-trader"
KEYS = ("dhan_client_id", "dhan_access_token")


def _kr():
    try:
        import keyring
        from keyring.backends import fail
        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def store(name: str, value: str) -> str:
    if name not in KEYS:
        raise ValueError("unknown secret")
    kr = _kr()
    if kr is None:
        raise RuntimeError("No OS credential store available. Put the value in the .env file instead (see .env.example).")
    kr.set_password(SERVICE, name, value)
    return "keyring"


def delete(name: str):
    kr = _kr()
    if kr is not None:
        try:
            kr.delete_password(SERVICE, name)
        except Exception:
            pass


def get(name: str) -> Optional[str]:
    kr = _kr()
    if kr is not None:
        try:
            v = kr.get_password(SERVICE, name)
            if v:
                return v
        except Exception:
            log.warning("keyring read failed for %s", name)
    return os.environ.get(name.upper()) or None


def mask(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    return ("*" * max(0, len(v) - 4)) + v[-4:] if len(v) > 4 else "****"


def token_expiry(token: Optional[str]) -> Optional[datetime]:
    """Dhan access tokens are JWTs; read the `exp` claim WITHOUT trusting it for auth (display/alerts only)."""
    if not token or token.count(".") != 2:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        return datetime.fromtimestamp(int(exp), tz=timezone.utc) if exp else None
    except Exception:
        return None


def status() -> dict:
    cid = get("dhan_client_id")
    tok = get("dhan_access_token")
    exp = token_expiry(tok)
    now = datetime.now(timezone.utc)
    return {
        "client_id": mask(cid), "access_token": "set" if tok else None,
        "token_expires_at": exp.isoformat() if exp else None,
        "token_expired": (exp is not None and exp <= now),
        "token_expires_in_s": int((exp - now).total_seconds()) if exp else None,
        "store": "keyring" if _kr() else "env",
    }
