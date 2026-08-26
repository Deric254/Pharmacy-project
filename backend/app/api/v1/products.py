from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.user import User
from app.schemas.batch import BatchCreate, BatchOut, BatchUpdate
from app.schemas.product import BulkImportResult, ProductCreate, ProductOut, ProductUpdate
from app.services.batch_service import BatchService
from app.services.product_import_service import bulk_import, generate_import_template
from app.services.product_service import ProductService
from app.services.report_export_service import ExportFormat, build_export_response

router = APIRouter(prefix="/products", tags=["products"])
_MAX_IMPORT_FILE_BYTES = 10 * 1024 * 1024

# Applies only to the JSON/browsing response below -- never to the CSV/XLSX
# export branch, which must always return every matching row regardless of
# count (that's the entire point of an export). This exists purely so a POS
# screen loading with no search term (or a cleared search box) can never
# pull an entire multi-thousand-SKU catalog over the wire and render it as
# one giant DOM list -- 200 matches the page-size cap already used for
# Sales and Audit Logs elsewhere in this app, kept for consistency.
_MAX_BROWSE_RESULTS = 200

_EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get(
    "/import-template",
    dependencies=[Depends(require_permission("products.manage"))],
)
async def download_import_template() -> Response:
    content = generate_import_template()
    return Response(
        content=content,
        media_type=_EXCEL_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="product-import-template.xlsx"'},
    )


@router.post(
    "/import",
    response_model=BulkImportResult,
    status_code=201,
    dependencies=[Depends(require_permission("products.manage"))],
)
async def import_products(
    file: Annotated[UploadFile, File()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BulkImportResult:
    file_bytes = await file.read()
    if len(file_bytes) > _MAX_IMPORT_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Import file is too large")
    return await bulk_import(db, file_bytes)


@router.get(
    "",
    dependencies=[Depends(require_permission("inventory.view"))],
)
async def list_products(
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str | None = Query(default=None, max_length=120),
    export: ExportFormat = "json",
) -> object:
    products = await ProductService(db).list_all(search=search)
    if export == "json":
        # Already sorted most-stocked-first by the service, so this slice
        # keeps the most relevant products, not an arbitrary cut.
        return products[:_MAX_BROWSE_RESULTS]
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


@router.patch("/{product_id}/batches/{batch_id}", response_model=BatchOut)
async def update_batch(
    product_id: int,
    batch_id: int,
    payload: BatchUpdate,
    current_user: Annotated[User, Depends(require_permission("batches.reprice"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BatchOut:
    return await BatchService(db).update_selling_price(
        product_id, batch_id, payload, changed_by=current_user
    )
