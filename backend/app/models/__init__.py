"""
Import every mapped model here so `import app.models` (done once, early,
from app/main.py) fully populates SQLAlchemy's declarative registry
before the app serves a single request.

Why this file has to exist: SQLAlchemy configures ALL registered
mappers together, lazily, the first time ANY ORM query compiles --
not just the mappers involved in that query. A relationship declared
with a string/forward reference (e.g. `Mapped[Category | None]` on
Product, resolved from a `TYPE_CHECKING`-only import) is only
resolvable if the referenced class has actually been imported
somewhere by then. Skip importing even one model class here and the
first real query anywhere in the app raises
`InvalidRequestError: ... failed to locate a name`, regardless of
whether that query touches the missing model at all.

Every new model module MUST be added to this list. There is no
compiler check for a forgotten one -- only the fact that the app
won't start serving traffic. `tests/conftest.py` also imports every
model directly for the same reason; keep both lists in sync when
adding a model.
"""

from app.models.ai_provider_key import AIProviderKey
from app.models.audit_log import AuditLog
from app.models.backup import BackupLog, BackupOAuthToken
from app.models.business_config import BusinessConfig
from app.models.category import Category
from app.models.customer import Customer
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem
from app.models.refund import Refund, RefundItem
from app.models.role import Permission, Role
from app.models.sale import Payment, Sale, SaleItem
from app.models.setup_lock import SetupLock
from app.models.stock_movement import StockMovement
from app.models.stock_take import StockTake, StockTakeItem
from app.models.supplier import Supplier, SupplierTransaction
from app.models.user import User, UserSession

__all__ = [
    "AIProviderKey",
    "AuditLog",
    "BackupLog",
    "BackupOAuthToken",
    "BusinessConfig",
    "Category",
    "Customer",
    "MedicineBatch",
    "Payment",
    "Permission",
    "Product",
    "PurchaseOrder",
    "PurchaseOrderItem",
    "Refund",
    "RefundItem",
    "Role",
    "Sale",
    "SaleItem",
    "SetupLock",
    "StockMovement",
    "StockTake",
    "StockTakeItem",
    "Supplier",
    "SupplierTransaction",
    "User",
    "UserSession",
]
