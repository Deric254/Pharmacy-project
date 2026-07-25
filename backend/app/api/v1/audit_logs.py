from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.schemas.audit_log import AuditLogPage
from app.services.audit_log_service import AuditLogService
from app.services.report_export_service import ExportFormat, build_export_response

router = APIRouter(tags=["audit"], dependencies=[Depends(require_permission("audit.view"))])


@router.get("/audit-logs")
async def list_audit_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    entity_type: str | None = None,
    action: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    export: ExportFormat = "json",
) -> object:
    service = AuditLogService(db)
    if export != "json":
        entries = await service.list_all_for_export(
            entity_type=entity_type, action=action, start_date=start_date, end_date=end_date
        )
        headers = ["Date/time", "Action", "By", "Entity", "Entity ID", "Was", "Now"]
        rows: list[list[object]] = [
            [
                e.created_at.isoformat(),
                e.action,
                e.user_name_snapshot or "System",
                e.entity_type,
                e.entity_id,
                e.old_value or "",
                e.new_value or "",
            ]
            for e in entries
        ]
        return build_export_response(export, entries, "Audit Trail", headers, rows)

    page: AuditLogPage = await service.list_entries(
        entity_type=entity_type,
        action=action,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    return page
