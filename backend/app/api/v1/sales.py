from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.user import User
from app.schemas.refund import RefundOut, RefundRequest
from app.schemas.sale import SaleCreate, SaleOut, SalePage
from app.services.refund_service import RefundService
from app.services.sale_service import SaleService

router = APIRouter(prefix="/sales", tags=["sales"])


@router.post("", response_model=SaleOut, status_code=201)
async def create_sale(
    payload: SaleCreate,
    cashier: Annotated[User, Depends(require_permission("sales.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SaleOut:
    return await SaleService(db).create_sale(payload, cashier)


@router.get("", response_model=SalePage, dependencies=[Depends(require_permission("sales.create"))])
async def list_sales(
    db: Annotated[AsyncSession, Depends(get_db)],
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> SalePage:
    return await SaleService(db).list_sales(start_date, end_date, limit, offset)


@router.get(
    "/{sale_id}", response_model=SaleOut, dependencies=[Depends(require_permission("sales.create"))]
)
async def get_sale(sale_id: int, db: Annotated[AsyncSession, Depends(get_db)]) -> SaleOut:
    return await SaleService(db).get_sale(sale_id)


@router.post("/{sale_id}/refunds", response_model=RefundOut, status_code=201)
async def create_refund(
    sale_id: int,
    payload: RefundRequest,
    processor: Annotated[User, Depends(require_permission("sales.refund"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RefundOut:
    return await RefundService(db).create_refund(sale_id, payload, processor)


@router.get(
    "/{sale_id}/refunds",
    response_model=list[RefundOut],
    dependencies=[Depends(require_permission("sales.refund"))],
)
async def list_refunds(
    sale_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> list[RefundOut]:
    return await RefundService(db).list_refunds_for_sale(sale_id)
