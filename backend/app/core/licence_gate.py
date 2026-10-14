"""
Offline per-computer licence gate.

The backend is the real gate: every /api/* request and WebSocket handshake
is refused (HTTP 402 / WebSocket close 4402) unless this copy holds a valid
licence, or is the owner's own computer. The UI only shows the activation
screen. Only the licence endpoints (app/api/v1/licence.py), /health and the
static front-end files stay reachable while locked -- the activation screen
needs them, and none of them expose business data.

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
STATE_FILENAME = "licence-state.json"
# Verifying a signature is cheap but not free; a minute of caching keeps the
# per-request cost at a dictionary lookup. Every activation resets it.
STATE_TTL_SECONDS = 60.0
# Sent when a WebSocket handshake is refused for want of a licence.
LICENCE_REQUIRED_CLOSE_CODE = 4402

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
                APP_ID, PREFIX, OWNER_PUBLIC_KEY, FileStore(str(_data_dir()), STATE_FILENAME)
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
    return state


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


class LicenceGateMiddleware:
    """Pure ASGI, not BaseHTTPMiddleware: it must cover WebSocket handshakes too."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and is_gated(scope["path"]):
            state = await current_state()
            if not state["ok"]:
                if scope["type"] == "http":
                    response = JSONResponse(
                        {
                            "detail": state["message"],
                            "licence": {"ok": False, "kind": state["kind"]},
                        },
                        status_code=402,
                    )
                    await response(scope, receive, send)
                else:
                    await receive()  # the websocket.connect event
                    await send({"type": "websocket.close", "code": LICENCE_REQUIRED_CLOSE_CODE})
                return
        await self.app(scope, receive, send)
