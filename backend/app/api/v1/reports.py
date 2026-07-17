from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.user import User
from app.schemas.reports import (
    FastSlowMoversOut,
    ProfitReportOut,
    ReceivingDiscrepancyReportOut,
    StockTakeHistoryOut,
)
from app.services.report_export_service import export_to_excel, export_to_pdf
from app.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])

ExportFormat = Literal["json", "excel", "pdf"]

_EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PDF_MEDIA_TYPE = "application/pdf"


def _require_export_permission_if_needed(export: ExportFormat, user: User) -> None:
    """
    reports.view and reports.export are distinct permissions (both
    seeded in migration 0001). Every current role that has one also
    has the other, so this distinction has no visible effect today --
    but it's checked properly now rather than left dead, so a future
    view-only role doesn't silently gain export rights.
    """
    if export == "json":
        return
    permission_codes = {p.code for p in user.role.permissions}
    if "reports.export" not in permission_codes:
        raise HTTPException(status_code=403, detail="Missing required permission: reports.export")


def _export_or_json(
    export: ExportFormat,
    json_payload: object,
    title: str,
    headers: list[str],
    rows: list[list[object]],
) -> object:
    if export == "excel":
        content = export_to_excel(headers, rows, sheet_title=title)
        return Response(
            content=content,
            media_type=_EXCEL_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{title}.xlsx"'},
        )
    if export == "pdf":
        content = export_to_pdf(title, headers, rows)
        return Response(
            content=content,
            media_type=_PDF_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{title}.pdf"'},
        )
    return json_payload


@router.get("/sales")
async def sales_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_permission("reports.view"))],
    start_date: date,
    end_date: date,
    group_by: Literal["day", "month"] = "day",
    export: ExportFormat = "json",
) -> object:
    _require_export_permission_if_needed(export, user)
    result = await ReportService(db).sales_summary(start_date, end_date, group_by)
    headers = ["Period", "Sale Count", "Total Revenue", "Total Discount"]
    rows = [[e.period, e.sale_count, e.total_revenue, e.total_discount] for e in result.entries]
    return _export_or_json(export, result, "Sales Summary", headers, rows)


@router.get(
    "/profit",
    response_model=ProfitReportOut,
    dependencies=[Depends(require_permission("reports.view_profit"))],
)
async def profit_report(
    db: Annotated[AsyncSession, Depends(get_db)], start_date: date, end_date: date
) -> ProfitReportOut:
    # Export intentionally not offered here at all, regardless of
    # permission -- profit is the most sensitive number in the system
    # (matches the ChemistOwner-only "sees profit" requirement from the
    # client discovery form); a downloadable copy would leave the audit
    # trail entirely. Kept JSON-only, no export param.
    return await ReportService(db).profit_report(start_date, end_date)


@router.get("/expired-stock")
async def expired_stock(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_permission("reports.view"))],
    export: ExportFormat = "json",
) -> object:
    _require_export_permission_if_needed(export, user)
    result = await ReportService(db).expired_stock()
    headers = ["Product", "Batch", "Expiry Date", "Days Expired", "Qty Remaining", "Value at Cost"]
    rows = [
        [
            e.product_name,
            e.batch_number,
            str(e.expiry_date),
            e.days_expired,
            e.qty_remaining,
            e.value_at_cost,
        ]
        for e in result.entries
    ]
    return _export_or_json(export, result, "Expired Stock", headers, rows)


@router.get(
    "/fast-slow-movers",
    response_model=FastSlowMoversOut,
    dependencies=[Depends(require_permission("reports.view"))],
)
async def fast_slow_movers(
    db: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(default=30, ge=1),
    limit: int = Query(default=10, ge=1, le=100),
) -> FastSlowMoversOut:
    return await ReportService(db).fast_slow_movers(days=days, limit=limit)


@router.get(
    "/receiving-discrepancies",
    response_model=ReceivingDiscrepancyReportOut,
    dependencies=[Depends(require_permission("reports.view"))],
)
async def receiving_discrepancies(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ReceivingDiscrepancyReportOut:
    return await ReportService(db).receiving_discrepancies()


@router.get(
    "/stock-take-history",
    response_model=StockTakeHistoryOut,
    dependencies=[Depends(require_permission("reports.view"))],
)
async def stock_take_history(db: Annotated[AsyncSession, Depends(get_db)]) -> StockTakeHistoryOut:
    return await ReportService(db).stock_take_history()
