from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.user import User
from app.schemas.batch import BatchCreate, BatchOut
from app.schemas.product import ProductCreate, ProductOut, ProductUpdate
from app.services.batch_service import BatchService
from app.services.product_service import ProductService
from app.services.report_export_service import ExportFormat, build_export_response

router = APIRouter(prefix="/products", tags=["products"])


@router.get(
    "",
    dependencies=[Depends(require_permission("inventory.view"))],
)
async def list_products(
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str | None = Query(default=None),
    export: ExportFormat = "json",
) -> object:
    products = await ProductService(db).list_all(search=search)
    if export == "json":
        return products
    headers = [
        "ID",
        "Name",
        "Barcode",
        "Unit",
        "Reorder point",
        "Selling price",
        "Qty on hand",
        "Active",
    ]
    rows: list[list[object]] = [
        [
            p.id,
            p.name,
            p.barcode or "",
            p.unit,
            p.reorder_point,
            p.default_selling_price,
            p.total_qty_available,
            "Yes" if p.is_active else "No",
        ]
        for p in products
    ]
    return build_export_response(export, products, "Products", headers, rows)


@router.get(
    "/barcode/{barcode}",
    response_model=ProductOut,
    dependencies=[Depends(require_permission("inventory.view"))],
)
async def get_product_by_barcode(
    barcode: str, db: Annotated[AsyncSession, Depends(get_db)]
) -> ProductOut:
    return await ProductService(db).get_by_barcode(barcode)


@router.get(
    "/{product_id}",
    response_model=ProductOut,
    dependencies=[Depends(require_permission("inventory.view"))],
)
async def get_product(product_id: int, db: Annotated[AsyncSession, Depends(get_db)]) -> ProductOut:
    return await ProductService(db).get(product_id)


@router.post("", response_model=ProductOut, status_code=201)
async def create_product(
    payload: ProductCreate,
    _: Annotated[User, Depends(require_permission("products.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProductOut:
    return await ProductService(db).create(payload)


@router.patch("/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: int,
    payload: ProductUpdate,
    _: Annotated[User, Depends(require_permission("products.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProductOut:
    return await ProductService(db).update(product_id, payload)


@router.delete("/{product_id}", status_code=204)
async def delete_product(
    product_id: int,
    _: Annotated[User, Depends(require_permission("products.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await ProductService(db).delete(product_id)


@router.get(
    "/{product_id}/batches",
    response_model=list[BatchOut],
    dependencies=[Depends(require_permission("inventory.view"))],
)
async def list_batches(
    product_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> list[BatchOut]:
    return await BatchService(db).list_for_product(product_id)


@router.post("/{product_id}/batches", response_model=BatchOut, status_code=201)
async def create_batch(
    product_id: int,
    payload: BatchCreate,
    current_user: Annotated[User, Depends(require_permission("batches.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BatchOut:
    return await BatchService(db).create_batch(product_id, payload, created_by=current_user)
