from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.schemas.customer import CustomerCreate, CustomerOut, PurchaseHistoryEntryOut
from app.services.customer_service import CustomerService

# Gated with sales.create (not a new permission) -- looking up or
# registering a customer is a normal part of the checkout flow, so
# whoever can make a sale should be able to do this too. See the
# module commit message for the reasoning against adding a separate
# customers.manage permission for this scope.
router = APIRouter(prefix="/customers", tags=["customers"])


@router.get(
    "", response_model=list[CustomerOut], dependencies=[Depends(require_permission("sales.create"))]
)
async def list_customers(
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str | None = Query(default=None),
) -> list[CustomerOut]:
    return await CustomerService(db).list_all(search=search)


@router.post(
    "",
    response_model=CustomerOut,
    status_code=201,
    dependencies=[Depends(require_permission("sales.create"))],
)
async def create_customer(
    payload: CustomerCreate, db: Annotated[AsyncSession, Depends(get_db)]
) -> CustomerOut:
    return await CustomerService(db).create(payload)


@router.get(
    "/phone/{phone}",
    response_model=CustomerOut,
    dependencies=[Depends(require_permission("sales.create"))],
)
async def get_customer_by_phone(
    phone: str, db: Annotated[AsyncSession, Depends(get_db)]
) -> CustomerOut:
    return await CustomerService(db).get_by_phone(phone)


@router.get(
    "/{customer_id}",
    response_model=CustomerOut,
    dependencies=[Depends(require_permission("sales.create"))],
)
async def get_customer(
    customer_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> CustomerOut:
    return await CustomerService(db).get(customer_id)


@router.get(
    "/{customer_id}/purchase-history",
    response_model=list[PurchaseHistoryEntryOut],
    dependencies=[Depends(require_permission("sales.create"))],
)
async def get_purchase_history(
    customer_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> list[PurchaseHistoryEntryOut]:
    return await CustomerService(db).get_purchase_history(customer_id)
