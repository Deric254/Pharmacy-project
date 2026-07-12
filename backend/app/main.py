"""
App entrypoint.

Routes are mounted under /api/v1. When a breaking API change is ever
needed, it ships as /api/v2 alongside v1 (not replacing it) until
clients have migrated — this is the forward/backward compatibility
contract at the API layer. See README.md "Compatibility Policy".
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.ai import router as ai_router
from app.api.v1.auth import router as auth_router
from app.api.v1.backups import router as backups_router
from app.api.v1.business_config import router as business_config_router
from app.api.v1.customers import router as customers_router
from app.api.v1.inventory import router as inventory_router
from app.api.v1.products import router as products_router
from app.api.v1.purchase_orders import router as purchase_orders_router
from app.api.v1.reports import router as reports_router
from app.api.v1.sales import router as sales_router
from app.api.v1.stock_takes import router as stock_takes_router
from app.api.v1.suppliers import router as suppliers_router
from app.api.v1.websocket import router as websocket_router
from app.core.config import get_settings
from app.core.websocket_manager import manager as ws_manager
from app.services.notification_dispatcher import start_dispatcher_task, stop_dispatcher_task

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    dispatcher_task = start_dispatcher_task(ws_manager)
    try:
        yield
    finally:
        await stop_dispatcher_task(dispatcher_task)


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
app.include_router(websocket_router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
