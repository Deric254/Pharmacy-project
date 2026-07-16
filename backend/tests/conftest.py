"""
Shared pytest fixtures. Tests run against a throwaway SQLite DB by
default (fast, no external services needed) — CI additionally runs the
same suite against real MySQL (see ci.yml) since SQLite doesn't enforce
everything MySQL does (e.g. some FK/constraint behaviors differ).
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")
os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")

import pytest_asyncio

from app.core.database import AsyncSessionLocal, Base, engine
from app.core.redis_client import aclose_for_current_loop, redis_client
from app.core.security import hash_password
from app.models.ai_provider_key import AIProviderKey  # noqa: F401
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.backup import BackupLog, BackupOAuthToken  # noqa: F401
from app.models.business_config import BusinessConfig  # noqa: F401
from app.models.category import Category  # noqa: F401
from app.models.customer import Customer  # noqa: F401
from app.models.medicine_batch import MedicineBatch  # noqa: F401
from app.models.product import Product  # noqa: F401
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem  # noqa: F401
from app.models.refund import Refund, RefundItem  # noqa: F401
from app.models.role import Permission, Role  # noqa: F401
from app.models.sale import Payment, Sale, SaleItem  # noqa: F401
from app.models.stock_movement import StockMovement  # noqa: F401
from app.models.stock_take import StockTake, StockTakeItem  # noqa: F401
from app.models.supplier import Supplier, SupplierTransaction  # noqa: F401
from app.models.user import User, UserSession  # noqa: F401


def running_on_sqlite() -> bool:
    """
    Shared by any test that must skip on SQLite because it doesn't
    enforce a behavior MySQL does by default (row-level locking via
    SELECT...FOR UPDATE, foreign key constraints). Centralized here
    instead of each test file redefining its own copy.
    """
    return "sqlite" in os.environ.get("DATABASE_URL", "sqlite")


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(autouse=True)
async def _clear_cache():
    """
    Config (and later, reports) use a Redis cache-aside pattern. Redis
    is shared across the whole test session, so without this, a config
    value cached by one test would leak into the next test's
    assertions -- a classic source of flaky, order-dependent tests.
    """
    yield
    await redis_client.flushdb()
    await aclose_for_current_loop()


@pytest_asyncio.fixture
async def seeded_roles():
    async with AsyncSessionLocal() as db:
        perms = {
            code: Permission(code=code, description=code)
            for code in [
                "sales.create",
                "sales.refund",
                "users.manage",
                "config.edit",
                "inventory.view",
                "inventory.adjust",
                "products.manage",
                "batches.create",
                "stocktake.perform",
                "stocktake.approve_variance",
                "purchasing.create_po",
                "purchasing.approve_po",
                "purchasing.receive_stock",
                "reports.view",
                "reports.export",
                "ai.use",
                "backups.manage",
            ]
        }
        db.add_all(perms.values())
        await db.flush()

        employee = Role(name="Employee")
        admin = Role(name="Administrator")
        owner = Role(name="ChemistOwner")
        employee.permissions = [
            perms["sales.create"],
            perms["inventory.view"],
            perms["stocktake.perform"],
            perms["ai.use"],
        ]
        admin.permissions = list(perms.values())
        owner.permissions = list(perms.values())
        db.add_all([employee, admin, owner])
        await db.commit()
        return {"Employee": employee.id, "Administrator": admin.id, "ChemistOwner": owner.id}


@pytest_asyncio.fixture
async def owner_user(seeded_roles):
    async with AsyncSessionLocal() as db:
        u = User(
            full_name="Lucy Kangai",
            username="lucy",
            hashed_password=hash_password("S3curePass!"),
            role_id=seeded_roles["ChemistOwner"],
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


@pytest_asyncio.fixture
async def employee_user(seeded_roles):
    async with AsyncSessionLocal() as db:
        u = User(
            full_name="Cashier Joe",
            username="joe",
            hashed_password=hash_password("pass1234"),
            role_id=seeded_roles["Employee"],
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


@pytest_asyncio.fixture
async def client():
    """
    httpx.AsyncClient over ASGITransport runs the app in-process on the
    SAME event loop as this fixture and the test itself. The older
    approach (Starlette's sync TestClient) spins the app up in a
    separate thread with its own event loop, which caused the shared
    Redis/DB async clients to bind to conflicting loops the moment a
    test touched both a fixture and an HTTP call -- surfaced as
    "attached to a different loop" errors. This is the correct fix,
    not a workaround.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
