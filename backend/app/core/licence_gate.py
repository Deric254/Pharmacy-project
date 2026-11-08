"""
Offline per-computer licence gate.

The backend is the real gate. A licence is a plan: 30 days ($100) or lifetime
($1000).
  * Valid, or up to GRACE_DAYS after it ends: everything works (the UI shows a
    renewal notice).
  * Past the grace period: READ-ONLY. The person can still sign in, read their
    data and take it out (every GET, the encrypted migration export, WebSockets),
    but every other call is refused with HTTP 402 plus where to renew.
  * Never activated, a wrong-computer licence or a wound-back clock: LOCKED.
    Every /api/* request and WebSocket handshake is refused (HTTP 402 /
    WebSocket close 4402). Only the licence endpoints
    (app/api/v1/licence.py), /health and the static front-end files stay
    reachable -- the activation screen needs them, and none of them expose
    business data.

Verification is Ed25519 against OWNER_PUBLIC_KEY (app/core/licence_owner_key.py,
built into every copy). A copy can check a licence but never make one. A build
without a valid public key refuses to run at all (fail closed); CI refuses to
ship one (backend/licensing/check_owner_key.py).

State lives in a small JSON file in the same data folder as the database, so
it needs no migration and never touches business data.
"""

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings
from app.core.licence_owner_key import OWNER_PUBLIC_KEY
from licensing.licensing import FileStore, Licensing

APP_ID = "Pharmacy ERP"
PREFIX = "PHE"
GRACE_DAYS = 5
# Where a person renews: the DericBI site, pre-filled with this app and the activation code.
RENEW_PAGE = "https://dericbi.vercel.app/renew.html"
RENEW_APP = "pharmacy-erp"
STATE_FILENAME = "licence-state.json"
# Verifying a signature is cheap but not free; a minute of caching keeps the
# per-request cost at a dictionary lookup. Every activation resets it.
STATE_TTL_SECONDS = 60.0
# Sent when a WebSocket handshake is refused for want of a licence.
LICENCE_REQUIRED_CLOSE_CODE = 4402
READ_ONLY_MESSAGE = (
    "Your licence has expired, so this is view-only. Renew your licence to add or change anything."
)
# What still works once the licence has run out: looking at data and taking it out
# (every GET/HEAD/OPTIONS, which covers reports and exports), signing in (including
# the steps that must finish before the app opens: first password change, terms,
# a forgotten password), and the one POST that is a download of the person's own
# data. Anything not listed here is treated as a change and refused.
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SIGN_IN_PATHS = frozenset(
    {
        "/auth/login",
        "/auth/refresh",
        "/auth/logout",
        "/auth/forgot-password",
        "/auth/change-password",
        "/auth/accept-terms",
    }
)
DATA_OUT_PATHS = frozenset({"/backups/export-for-migration"})

_lock = threading.Lock()
_licensing: Licensing | None = None
_cached: tuple[float, dict[str, Any]] | None = None


def _data_dir() -> Path:
    """The folder that holds the database (same rule as Settings.log_file_path)."""
    prefix = "sqlite+aiosqlite:///"
    url = get_settings().database_url
    if not url.startswith(prefix):
        return Path(".").resolve()
    return Path(url[len(prefix) :]).resolve().parent


def get_licensing() -> Licensing:
    global _licensing
    with _lock:
        if _licensing is None:
            _licensing = Licensing(
                APP_ID,
                PREFIX,
                OWNER_PUBLIC_KEY,
                FileStore(str(_data_dir()), STATE_FILENAME),
                grace_days=GRACE_DAYS,
            )
        return _licensing


def use_licensing(licensing: Licensing | None) -> None:
    """Swap the Licensing instance (tests use a throwaway key); None restores the real one."""
    global _licensing
    with _lock:
        _licensing = licensing
    reset_cache()


def reset_cache() -> None:
    global _cached
    with _lock:
        _cached = None


def public_state() -> dict[str, Any]:
    """Licence state for the UI, plus the activation code needed to request or renew one."""
    lic = get_licensing()
    state = dict(lic.state())
    if lic.configured():
        state["install_id"] = lic.install_id()
        state["renew_url"] = renew_url(state["install_id"])
    return state


def renew_url(install_id: str) -> str:
    return f"{RENEW_PAGE}?app={RENEW_APP}&code={install_id}"


def _compute_state() -> dict[str, Any]:
    return dict(get_licensing().state())


async def current_state() -> dict[str, Any]:
    """The cached answer to "may this copy be used right now?", refreshed off the event loop."""
    global _cached
    now = time.monotonic()
    with _lock:
        cached = _cached
    if cached is not None and now - cached[0] < STATE_TTL_SECONDS:
        return cached[1]
    state = await asyncio.to_thread(_compute_state)
    with _lock:
        _cached = (now, state)
    return state


def is_gated(path: str) -> bool:
    prefix = get_settings().api_v1_prefix
    if not path.startswith("/api/"):
        return False
    return not path.startswith(f"{prefix}/licence/")


def allows(scope: Scope, state: dict[str, Any]) -> bool:
    """May this call run? Everything for a valid licence; reading, signing in and exporting once it
    has lapsed; nothing otherwise."""
    if state["ok"]:
        return True
    if not state.get("read_only"):
        return False
    if scope["type"] == "websocket":
        return True
    if scope["method"] in READ_METHODS:
        return True
    prefix = get_settings().api_v1_prefix
    path = scope["path"]
    return scope["method"] == "POST" and any(
        path == f"{prefix}{p}" for p in SIGN_IN_PATHS | DATA_OUT_PATHS
    )


def refusal(state: dict[str, Any]) -> dict[str, Any]:
    """The JSON body sent with HTTP 402 when a call is refused for want of a licence."""
    if state.get("read_only"):
        return {
            "detail": READ_ONLY_MESSAGE,
            "licence": {
                "ok": False,
                "kind": state["kind"],
                "read_only": True,
                "renew_url": renew_url(state["install_id"]),
            },
        }
    return {"detail": state["message"], "licence": {"ok": False, "kind": state["kind"]}}


class LicenceGateMiddleware:
    """Pure ASGI, not BaseHTTPMiddleware: it must cover WebSocket handshakes too."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and is_gated(scope["path"]):
            state = await current_state()
            if not allows(scope, state):
                if scope["type"] == "http":
                    response = JSONResponse(refusal(state), status_code=402)
                    await response(scope, receive, send)
                else:
                    await receive()  # the websocket.connect event
                    await send({"type": "websocket.close", "code": LICENCE_REQUIRED_CLOSE_CODE})
                return
        await self.app(scope, receive, send)
