from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.user import User
from app.schemas.sale import SaleCreate, SaleOut
from app.services.sale_service import SaleService

router = APIRouter(prefix="/sales", tags=["sales"])


@router.post("", response_model=SaleOut, status_code=201)
async def create_sale(
    payload: SaleCreate,
    cashier: Annotated[User, Depends(require_permission("sales.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SaleOut:
    return await SaleService(db).create_sale(payload, cashier)


@router.get(
    "/{sale_id}", response_model=SaleOut, dependencies=[Depends(require_permission("sales.create"))]
)
async def get_sale(sale_id: int, db: Annotated[AsyncSession, Depends(get_db)]) -> SaleOut:
    return await SaleService(db).get_sale(sale_id)
