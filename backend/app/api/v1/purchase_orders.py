from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.user import User
from app.schemas.purchase_order import PurchaseOrderOut, QuickPurchaseRequest
from app.services.purchase_order_import_service import (
    bulk_import_purchase_order,
    generate_purchase_order_import_template,
)
from app.services.purchasing_service import PurchasingService

router = APIRouter(prefix="/purchase-orders", tags=["purchasing"])

_EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get(
    "/import-template",
    dependencies=[Depends(require_permission("purchasing.create_po"))],
)
async def download_po_import_template() -> Response:
    content = generate_purchase_order_import_template()
    return Response(
        content=content,
        media_type=_EXCEL_MEDIA_TYPE,
        headers={
            "Content-Disposition": 'attachment; filename="purchase-order-import-template.xlsx"'
        },
    )


@router.post(
    "/import",
    response_model=PurchaseOrderOut,
    status_code=201,
    dependencies=[Depends(require_permission("purchasing.create_po"))],
)
async def import_purchase_order(
    file: Annotated[UploadFile, File(max_length=10 * 1024 * 1024)],
    user: Annotated[User, Depends(require_permission("purchasing.create_po"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    supplier_id: Annotated[int, Form()],
) -> PurchaseOrderOut:
    file_bytes = await file.read()
    return await bulk_import_purchase_order(db, file_bytes, supplier_id, user)


@router.get(
    "",
    response_model=list[PurchaseOrderOut],
    dependencies=[Depends(require_permission("purchasing.create_po"))],
)
async def list_purchase_orders(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[PurchaseOrderOut]:
    """
    Every purchase order is created already RECEIVED -- stock only
    ever enters through quick_purchase, direct-receive, so there is no
    draft/sent/in-transit pipeline to show. This is just the log of
    what's been received, newest first.
    """
    return await PurchasingService(db).list_all()


@router.post(
    "/quick-purchase",
    response_model=PurchaseOrderOut,
    status_code=201,
    dependencies=[Depends(require_permission("purchasing.create_po"))],
)
async def quick_purchase(
    payload: QuickPurchaseRequest,
    user: Annotated[User, Depends(require_permission("purchasing.create_po"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PurchaseOrderOut:
    return await PurchasingService(db).quick_purchase(payload, user)


@router.get(
    "/{po_id}",
    response_model=PurchaseOrderOut,
    dependencies=[Depends(require_permission("purchasing.create_po"))],
)
async def get_purchase_order(
    po_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> PurchaseOrderOut:
    return await PurchasingService(db).get(po_id)
