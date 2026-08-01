from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.user import User
from app.schemas.stock_take import CountSubmit, StockTakeCreate, StockTakeItemOut, StockTakeOut
from app.services.stock_take_import_service import generate_count_template, import_counts
from app.services.stock_take_service import StockTakeService

router = APIRouter(prefix="/stock-takes", tags=["stock-takes"])

_EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get(
    "",
    response_model=list[StockTakeOut],
    dependencies=[Depends(require_permission("stocktake.perform"))],
)
async def list_stock_takes(db: Annotated[AsyncSession, Depends(get_db)]) -> list[StockTakeOut]:
    return await StockTakeService(db).list_all()


@router.post("", response_model=StockTakeOut, status_code=201)
async def initiate_stock_take(
    payload: StockTakeCreate,
    user: Annotated[User, Depends(require_permission("stocktake.perform"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StockTakeOut:
    return await StockTakeService(db).initiate(payload, user)


@router.get(
    "/{stock_take_id}",
    response_model=StockTakeOut,
    dependencies=[Depends(require_permission("stocktake.perform"))],
)
async def get_stock_take(
    stock_take_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> StockTakeOut:
    return await StockTakeService(db).get(stock_take_id)


@router.get(
    "/{stock_take_id}/count-template",
    dependencies=[Depends(require_permission("stocktake.perform"))],
)
async def download_count_template(
    stock_take_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> Response:
    content = await generate_count_template(db, stock_take_id)
    return Response(
        content=content,
        media_type=_EXCEL_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="stock-count-{stock_take_id}-template.xlsx"'
            )
        },
    )


@router.post(
    "/{stock_take_id}/import-counts",
    response_model=StockTakeOut,
    dependencies=[Depends(require_permission("stocktake.perform"))],
)
async def upload_counts(
    stock_take_id: int,
    file: UploadFile,
    user: Annotated[User, Depends(require_permission("stocktake.perform"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StockTakeOut:
    file_bytes = await file.read()
    return await import_counts(db, stock_take_id, file_bytes, user)


@router.post("/{stock_take_id}/items/{item_id}/count", response_model=StockTakeItemOut)
async def submit_count(
    stock_take_id: int,
    item_id: int,
    payload: CountSubmit,
    user: Annotated[User, Depends(require_permission("stocktake.perform"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StockTakeItemOut:
    return await StockTakeService(db).submit_count(stock_take_id, item_id, payload, user)


@router.post("/{stock_take_id}/items/{item_id}/approve", response_model=StockTakeItemOut)
async def approve_variance(
    stock_take_id: int,
    item_id: int,
    user: Annotated[User, Depends(require_permission("stocktake.approve_variance"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StockTakeItemOut:
    return await StockTakeService(db).approve_variance(stock_take_id, item_id, user)


@router.post("/{stock_take_id}/close", response_model=StockTakeOut)
async def close_stock_take(
    stock_take_id: int,
    user: Annotated[User, Depends(require_permission("stocktake.perform"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StockTakeOut:
    return await StockTakeService(db).close(stock_take_id, user)
