from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.user import User
from app.schemas.purchase_order import (
    PurchaseOrderCreate,
    PurchaseOrderOut,
    ReceiveRequest,
    ReceiveResponse,
    ReconcileRequest,
)
from app.services.purchasing_service import PurchasingService

router = APIRouter(prefix="/purchase-orders", tags=["purchasing"])


@router.get(
    "/kanban",
    response_model=dict[str, list[PurchaseOrderOut]],
    dependencies=[Depends(require_permission("purchasing.create_po"))],
)
async def kanban_board(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, list[PurchaseOrderOut]]:
    return await PurchasingService(db).get_kanban()


@router.post("", response_model=PurchaseOrderOut, status_code=201)
async def create_purchase_order(
    payload: PurchaseOrderCreate,
    user: Annotated[User, Depends(require_permission("purchasing.create_po"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PurchaseOrderOut:
    return await PurchasingService(db).create(payload, user)


@router.get(
    "/{po_id}",
    response_model=PurchaseOrderOut,
    dependencies=[Depends(require_permission("purchasing.create_po"))],
)
async def get_purchase_order(
    po_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> PurchaseOrderOut:
    return await PurchasingService(db).get(po_id)


@router.post("/{po_id}/send", response_model=PurchaseOrderOut)
async def send_purchase_order(
    po_id: int,
    user: Annotated[User, Depends(require_permission("purchasing.approve_po"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PurchaseOrderOut:
    return await PurchasingService(db).send(po_id, user)


@router.post("/{po_id}/mark-in-transit", response_model=PurchaseOrderOut)
async def mark_in_transit(
    po_id: int,
    user: Annotated[User, Depends(require_permission("purchasing.create_po"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PurchaseOrderOut:
    return await PurchasingService(db).mark_in_transit(po_id, user)


@router.post("/{po_id}/receive", response_model=ReceiveResponse)
async def receive_purchase_order(
    po_id: int,
    payload: ReceiveRequest,
    user: Annotated[User, Depends(require_permission("purchasing.receive_stock"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ReceiveResponse:
    return await PurchasingService(db).receive(po_id, payload, user)


@router.post("/{po_id}/reconcile", response_model=PurchaseOrderOut)
async def reconcile_purchase_order(
    po_id: int,
    payload: ReconcileRequest,
    user: Annotated[User, Depends(require_permission("purchasing.approve_po"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PurchaseOrderOut:
    return await PurchasingService(db).reconcile(po_id, payload, user)
