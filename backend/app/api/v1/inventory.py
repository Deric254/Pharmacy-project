from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.user import User
from app.schemas.inventory import (
    AdjustmentOut,
    AdjustmentRequest,
    BulkWriteOffResult,
    ExpiringBatchOut,
    LowStockProductOut,
    ReconciliationIssueOut,
    StockValuationOut,
    WriteOffResult,
)
from app.services.inventory_service import InventoryService

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get(
    "/low-stock",
    response_model=list[LowStockProductOut],
    dependencies=[Depends(require_permission("inventory.view"))],
)
async def low_stock(db: Annotated[AsyncSession, Depends(get_db)]) -> list[LowStockProductOut]:
    return await InventoryService(db).get_low_stock_products()


@router.get(
    "/expiring",
    response_model=list[ExpiringBatchOut],
    dependencies=[Depends(require_permission("inventory.view"))],
)
async def expiring(
    db: Annotated[AsyncSession, Depends(get_db)],
    within_days: int | None = Query(default=None, ge=1),
) -> list[ExpiringBatchOut]:
    return await InventoryService(db).get_expiring_batches(within_days=within_days)


@router.get(
    "/valuation",
    response_model=StockValuationOut,
    dependencies=[Depends(require_permission("inventory.view"))],
)
async def valuation(db: Annotated[AsyncSession, Depends(get_db)]) -> StockValuationOut:
    return await InventoryService(db).get_valuation()


@router.post("/adjustments", response_model=AdjustmentOut, status_code=201)
async def create_adjustment(
    payload: AdjustmentRequest,
    user: Annotated[User, Depends(require_permission("inventory.adjust"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdjustmentOut:
    return await InventoryService(db).adjust_stock(payload, user)


@router.post("/batches/{batch_id}/write-off-expired", response_model=WriteOffResult)
async def write_off_expired_batch(
    batch_id: int,
    user: Annotated[User, Depends(require_permission("inventory.adjust"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WriteOffResult:
    return await InventoryService(db).write_off_expired_batch(batch_id, user)


@router.post("/write-off-all-expired", response_model=BulkWriteOffResult)
async def write_off_all_expired(
    user: Annotated[User, Depends(require_permission("inventory.adjust"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BulkWriteOffResult:
    return await InventoryService(db).write_off_all_expired(user)


@router.get(
    "/reconcile",
    response_model=list[ReconciliationIssueOut],
    dependencies=[Depends(require_permission("inventory.adjust"))],
)
async def reconcile(db: Annotated[AsyncSession, Depends(get_db)]) -> list[ReconciliationIssueOut]:
    return await InventoryService(db).reconcile()
