from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.schemas.customer import CustomerCreate, CustomerOut, PurchaseHistoryEntryOut
from app.services.customer_service import CustomerService
from app.services.report_export_service import ExportFormat, build_export_response

# Gated with sales.create (not a new permission) -- looking up or
# registering a customer is a normal part of the checkout flow, so
# whoever can make a sale should be able to do this too. See the
# module commit message for the reasoning against adding a separate
# customers.manage permission for this scope.
router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", dependencies=[Depends(require_permission("sales.create"))])
async def list_customers(
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str | None = Query(default=None),
    export: ExportFormat = "json",
) -> object:
    customers = await CustomerService(db).list_all(search=search)
    if export == "json":
        return customers
    headers = ["ID", "Name", "Phone", "Email", "Loyalty points"]
    rows: list[list[object]] = [
        [c.id, c.name, c.phone or "", c.email or "", c.loyalty_points] for c in customers
    ]
    return build_export_response(export, customers, "Customers", headers, rows)


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
