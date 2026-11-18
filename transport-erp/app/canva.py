"""Canva Connect API integration.

Flow: connect once (OAuth 2.0 with PKCE) -> "Edit in Canva" uploads a design's PNG as a Canva asset and
creates a Canva design from it -> you edit it in Canva (phone app or web) -> "Bring back from Canva"
exports it as PNG into the ERP, ready for the Social Media Manager to post.

Setup: create an integration at canva.com/developers, add the redirect URL shown in ERP Settings, enable
the scopes below, then put CANVA_CLIENT_ID and CANVA_CLIENT_SECRET in .env.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from . import db

AUTH_URL = "https://www.canva.com/api/oauth/authorize"
API = "https://api.canva.com/rest/v1"
SCOPES = "asset:read asset:write design:content:read design:content:write design:meta:read profile:read"


class CanvaError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.environ.get("CANVA_CLIENT_ID") and os.environ.get("CANVA_CLIENT_SECRET"))


def redirect_uri() -> str:
    return os.environ.get("CANVA_REDIRECT_URI") or f"http://127.0.0.1:{os.environ.get('APP_PORT', '8100')}/api/canva/callback"


def status() -> dict:
    tok = db.get_integration("canva")
    return {"configured": configured(), "connected": bool(tok.get("refresh_token")), "redirect_uri": redirect_uri()}


def auth_url() -> str:
    if not configured():
        raise CanvaError("Set CANVA_CLIENT_ID and CANVA_CLIENT_SECRET in .env first")
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    db.set_integration("canva_pending", {"verifier": verifier, "state": state, "at": time.time()})
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "code_challenge": challenge, "code_challenge_method": "s256", "scope": SCOPES, "response_type": "code",
        "client_id": os.environ["CANVA_CLIENT_ID"], "state": state, "redirect_uri": redirect_uri(),
    })


def _token_request(form: dict) -> dict:
    basic = base64.b64encode(f"{os.environ['CANVA_CLIENT_ID']}:{os.environ['CANVA_CLIENT_SECRET']}".encode()).decode()
    req = urllib.request.Request(f"{API}/oauth/token", data=urllib.parse.urlencode(form).encode(), method="POST",
                                 headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise CanvaError(f"Canva token error {e.code}: {e.read()[:300].decode(errors='replace')}") from e
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    db.set_integration("canva", tok)
    return tok


def finish_auth(code: str, state: str) -> None:
    pending = db.get_integration("canva_pending")
    if not pending or not secrets.compare_digest(pending.get("state", ""), state or "") or time.time() - pending.get("at", 0) > 900:
        raise CanvaError("Canva sign-in expired or invalid. Please try Connect again.")
    _token_request({"grant_type": "authorization_code", "code": code, "code_verifier": pending["verifier"], "redirect_uri": redirect_uri()})
    db.set_integration("canva_pending", None)


def disconnect() -> None:
    db.set_integration("canva", None)


def _access_token() -> str:
    tok = db.get_integration("canva")
    if not tok.get("refresh_token") and not tok.get("access_token"):
        raise CanvaError("Canva is not connected. Open Settings -> Connect Canva.")
    if time.time() >= tok.get("expires_at", 0):
        new = _token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        new.setdefault("refresh_token", tok["refresh_token"])
        db.set_integration("canva", new)
        tok = new
    return tok["access_token"]


def _api(method: str, path: str, body: dict | None = None, raw: bytes | None = None, headers: dict | None = None) -> dict:
    h = {"Authorization": f"Bearer {_access_token()}", **(headers or {})}
    data = raw
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(API + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise CanvaError(f"Canva API {e.code} on {path}: {e.read()[:300].decode(errors='replace')}") from e


def _wait(path: str, tries: int = 30) -> dict:
    for _ in range(tries):
        job = _api("GET", path)["job"]
        if job["status"] == "success":
            return job
        if job["status"] == "failed":
            raise CanvaError(f"Canva job failed: {job.get('error')}")
        time.sleep(2)
    raise CanvaError("Canva is taking too long; try again in a minute")


def upload_png(png: bytes, name: str) -> str:
    meta = {"name_base64": base64.b64encode(name[:50].encode()).decode()}
    job = _api("POST", "/asset-uploads", raw=png, headers={"Content-Type": "application/octet-stream", "Asset-Upload-Metadata": json.dumps(meta)})["job"]
    if job["status"] != "success":
        job = _wait(f"/asset-uploads/{job['id']}")
    return job["asset"]["id"]


def create_design(asset_id: str, title: str, width: int = 1080, height: int = 1080) -> dict:
    d = _api("POST", "/designs", {"design_type": {"type": "custom", "width": width, "height": height}, "asset_id": asset_id, "title": title[:255]})["design"]
    return {"id": d["id"], "edit_url": d.get("urls", {}).get("edit_url"), "view_url": d.get("urls", {}).get("view_url")}


def export_png(design_id: str) -> bytes:
    job = _api("POST", "/exports", {"design_id": design_id, "format": {"type": "png"}})["job"]
    if job["status"] != "success":
        job = _wait(f"/exports/{job['id']}")
    with urllib.request.urlopen(job["urls"][0], timeout=60) as r:
        return r.read()


def list_designs(query: str = "") -> list[dict]:
    items = _api("GET", "/designs" + (f"?query={urllib.parse.quote(query)}" if query else "")).get("items", [])
    return [{"id": i["id"], "title": i.get("title"), "thumbnail": (i.get("thumbnail") or {}).get("url"),
             "edit_url": (i.get("urls") or {}).get("edit_url")} for i in items]
