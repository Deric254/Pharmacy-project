from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.schemas.supplier import PaymentRecordRequest, SupplierCreate, SupplierOut
from app.services.supplier_service import SupplierService

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get(
    "",
    response_model=list[SupplierOut],
    dependencies=[Depends(require_permission("purchasing.create_po"))],
)
async def list_suppliers(db: Annotated[AsyncSession, Depends(get_db)]) -> list[SupplierOut]:
    return await SupplierService(db).list_all()


@router.post(
    "",
    response_model=SupplierOut,
    status_code=201,
    dependencies=[Depends(require_permission("purchasing.create_po"))],
)
async def create_supplier(
    payload: SupplierCreate, db: Annotated[AsyncSession, Depends(get_db)]
) -> SupplierOut:
    return await SupplierService(db).create(payload)


@router.get(
    "/{supplier_id}",
    response_model=SupplierOut,
    dependencies=[Depends(require_permission("purchasing.create_po"))],
)
async def get_supplier(
    supplier_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> SupplierOut:
    return await SupplierService(db).get(supplier_id)


@router.post(
    "/{supplier_id}/payments",
    response_model=SupplierOut,
    dependencies=[Depends(require_permission("purchasing.approve_po"))],
)
async def record_payment(
    supplier_id: int, payload: PaymentRecordRequest, db: Annotated[AsyncSession, Depends(get_db)]
) -> SupplierOut:
    return await SupplierService(db).record_payment(supplier_id, payload)
