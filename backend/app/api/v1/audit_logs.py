from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.schemas.audit_log import AuditLogPage
from app.services.audit_log_service import AuditLogService

router = APIRouter(tags=["audit"], dependencies=[Depends(require_permission("audit.view"))])


@router.get("/audit-logs", response_model=AuditLogPage)
async def list_audit_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    entity_type: str | None = None,
    action: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditLogPage:
    return await AuditLogService(db).list_entries(
        entity_type=entity_type,
        action=action,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
