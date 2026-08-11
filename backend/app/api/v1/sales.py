from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.customer import Customer
from app.models.sale import Sale
from app.models.user import User
from app.schemas.refund import RefundOut, RefundRequest
from app.schemas.sale import SaleCreate, SaleOut, SalePage
from app.services.business_config_service import BusinessConfigService
from app.services.receipt_service import generate_receipt_pdf
from app.services.refund_service import RefundService
from app.services.sale_service import SaleService

router = APIRouter(prefix="/sales", tags=["sales"])

_PDF_MEDIA_TYPE = "application/pdf"


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


@router.get(
    "/{sale_id}/receipt",
    dependencies=[Depends(require_permission("sales.create"))],
)
async def get_sale_receipt(sale_id: int, db: Annotated[AsyncSession, Depends(get_db)]) -> Response:
    result = await db.execute(select(Sale).where(Sale.id == sale_id))
    sale = result.scalar_one_or_none()
    if sale is None:
        raise HTTPException(status_code=404, detail="Sale not found")

    cashier = await db.get(User, sale.cashier_user_id)
    cashier_name = cashier.full_name if cashier is not None else "Unknown"

    customer_name = None
    if sale.customer_id is not None:
        customer = await db.get(Customer, sale.customer_id)
        customer_name = customer.name if customer is not None else None

    config = await BusinessConfigService(db).get()

    # generate_receipt_pdf is genuinely CPU-bound (reportlab layout +
    # PIL image decode for the logo) -- calling it directly here would
    # block this process's single asyncio event loop for the entire
    # duration, freezing every OTHER request the app is handling at
    # that moment (other cashiers' sales, dashboard loads, everything)
    # until this one receipt finishes rendering. run_in_threadpool
    # moves the actual rendering work onto a separate OS thread so the
    # event loop stays free to keep serving everyone else while this
    # one receipt builds.
    content = await run_in_threadpool(
        generate_receipt_pdf,
        sale=sale,
        cashier_name=cashier_name,
        customer_name=customer_name,
        business_name=config.business_name,
        business_address=config.address,
        business_phone=config.contact_phone,
        logo_url=config.logo_url,
        currency=config.currency,
        tax_id=config.tax_id,
        header_text=config.receipt_header_text or None,
        footer_text=config.receipt_footer_text or None,
        timezone=config.timezone,
    )

    return Response(
        content=content,
        media_type=_PDF_MEDIA_TYPE,
        headers={"Content-Disposition": f'inline; filename="Receipt-{sale_id}.pdf"'},
    )


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
