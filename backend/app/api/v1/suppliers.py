from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import get_current_user, require_permission
from app.models.user import User
from app.schemas.supplier import PaymentRecordRequest, SupplierCreate, SupplierOut
from app.services.supplier_service import SupplierService

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


def _require_purchasing_access(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    user_permission_codes = {p.code for p in current_user.role.permissions}
    if (
        "purchasing.create_po" not in user_permission_codes
        and "purchasing.approve_po" not in user_permission_codes
    ):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Missing required permission: purchasing.create_po or purchasing.approve_po",
        )
    return current_user


@router.get(
    "",
    response_model=list[SupplierOut],
    dependencies=[Depends(_require_purchasing_access)],
)
async def list_suppliers(db: Annotated[AsyncSession, Depends(get_db)]) -> list[SupplierOut]:
    return await SupplierService(db).list_all()


@router.post(
    "",
    response_model=SupplierOut,
    status_code=201,
    dependencies=[Depends(_require_purchasing_access)],
)
async def create_supplier(
    payload: SupplierCreate, db: Annotated[AsyncSession, Depends(get_db)]
) -> SupplierOut:
    return await SupplierService(db).create(payload)


@router.get(
    "/{supplier_id}",
    response_model=SupplierOut,
    dependencies=[Depends(_require_purchasing_access)],
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
