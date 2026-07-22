"""
App entrypoint.

Routes are mounted under /api/v1. When a breaking API change is ever
needed, it ships as /api/v2 alongside v1 (not replacing it) until
clients have migrated — this is the forward/backward compatibility
contract at the API layer. See README.md "Compatibility Policy".
"""

import math
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__

# Importing this populates SQLAlchemy's full mapper registry before
# the app serves any request -- see app/models/__init__.py for why
# that has to happen somewhere, unconditionally, every time. Imported
# under an alias so it doesn't collide with the `app = FastAPI(...)`
# variable defined further down in this same module.
from app import models as _models  # noqa: F401
from app.api.v1.ai import router as ai_router
from app.api.v1.auth import router as auth_router
from app.api.v1.backups import router as backups_router
from app.api.v1.business_config import router as business_config_router
from app.api.v1.customers import router as customers_router
from app.api.v1.inventory import router as inventory_router
from app.api.v1.products import router as products_router
from app.api.v1.purchase_orders import router as purchase_orders_router
from app.api.v1.reports import router as reports_router
from app.api.v1.roles import router as roles_router
from app.api.v1.sales import router as sales_router
from app.api.v1.setup import router as setup_router
from app.api.v1.stock_takes import router as stock_takes_router
from app.api.v1.suppliers import router as suppliers_router
from app.api.v1.users import router as users_router
from app.api.v1.websocket import router as websocket_router
from app.core.config import get_settings
from app.core.redis_client import aclose_for_current_loop
from app.core.websocket_manager import manager as ws_manager
from app.services.notification_dispatcher import start_dispatcher_task, stop_dispatcher_task

settings = get_settings()


def _frontend_dist_dir() -> Path | None:
    """
    Locate a built frontend (frontend/dist) to serve directly, if one
    is sitting next to this backend. Two cases:

    - Bundled desktop .exe (PyInstaller): `sys.frozen` is set, and the
      frontend build was packaged as data under "frontend_dist"
      relative to the extraction root (`sys._MEIPASS` for a onefile
      build). See pyinstaller/pharmacy-erp.spec.
    - Plain `python -m uvicorn ...` with a pre-built frontend sitting
      at ../frontend/dist relative to this file -- useful for a
      single-process run without Vite's dev server.

    Returns None (the common case: local dev, Vite's own dev server
    handles the frontend) if neither location has anything real in it.
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidate = base / "frontend_dist"
    else:
        candidate = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    return candidate if candidate.is_dir() else None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    dispatcher_task = start_dispatcher_task(ws_manager)
    try:
        yield
    finally:
        await stop_dispatcher_task(dispatcher_task)
        await aclose_for_current_loop()


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _sanitize_for_json(value: Any) -> Any:
    """
    Recursively replace non-finite floats (inf, -inf, nan) with their
    string representation. Only ever touches values already destined
    for an error message -- never business data.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_json(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    FastAPI's default handler for this exact exception type JSON-
    encodes the invalid input value verbatim inside each error detail
    (e.g. `"input": inf`) so the client can see what it sent -- normal
    and useful, except that Starlette's JSONResponse is RFC-strict
    (`allow_nan=False`) and raises a plain ValueError the instant that
    echoed value is itself non-finite. The result, confirmed by
    actually sending `Infinity` as a price: a request that Pydantic
    correctly rejected still 500s, because the rejection message
    itself couldn't be serialized. This produces the exact same 422
    the default handler would (jsonable_encoder first, same as
    FastAPI's own handler uses, since error details can also contain
    raw exception objects that aren't JSON-serializable at all), with
    one addition: any non-finite float left over is stringified too.
    """
    encoded = jsonable_encoder(exc.errors())
    return JSONResponse(
        status_code=422,
        content={"detail": _sanitize_for_json(encoded)},
    )


app.include_router(auth_router, prefix=settings.api_v1_prefix)
app.include_router(business_config_router, prefix=settings.api_v1_prefix)
app.include_router(products_router, prefix=settings.api_v1_prefix)
app.include_router(sales_router, prefix=settings.api_v1_prefix)
app.include_router(inventory_router, prefix=settings.api_v1_prefix)
app.include_router(stock_takes_router, prefix=settings.api_v1_prefix)
app.include_router(suppliers_router, prefix=settings.api_v1_prefix)
app.include_router(purchase_orders_router, prefix=settings.api_v1_prefix)
app.include_router(customers_router, prefix=settings.api_v1_prefix)
app.include_router(reports_router, prefix=settings.api_v1_prefix)
app.include_router(ai_router, prefix=settings.api_v1_prefix)
app.include_router(backups_router, prefix=settings.api_v1_prefix)
app.include_router(users_router, prefix=settings.api_v1_prefix)
app.include_router(roles_router, prefix=settings.api_v1_prefix)
app.include_router(setup_router, prefix=settings.api_v1_prefix)
app.include_router(websocket_router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


# Must be registered LAST: FastAPI/Starlette match routes in
# registration order, and this is a catch-all. Every API route above
# already claims its own path first, so this only ever runs for
# requests nothing else handled.
_frontend_dist = _frontend_dist_dir()
if _frontend_dist is not None:
    app.mount(
        "/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="frontend-assets"
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str) -> FileResponse:
        # An unmatched /api/... path is a real 404, not "serve the SPA
        # shell and let React Router silently show a blank page."
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")

        assert _frontend_dist is not None  # narrowed at import time; mypy can't see that here
        candidate = _frontend_dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # Anything else (/, /pos, /inventory, a hard refresh on a deep
        # React Router route, ...) falls back to the SPA shell, which
        # is what makes client-side routing work on a real reload.
        return FileResponse(_frontend_dist / "index.html")
