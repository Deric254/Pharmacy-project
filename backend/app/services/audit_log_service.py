from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.schemas.audit_log import AuditLogOut, AuditLogPage

MAX_PAGE_SIZE = 200


class AuditLogService:
    """
    Deliberately the only way this data is ever read back. The table
    itself has no client-facing create endpoint anywhere -- every row
    is written internally by whichever service performed the action
    being logged (see auth_service.py, role_service.py, etc.), never
    by a request that could be forged.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_entries(
        self,
        *,
        entity_type: str | None = None,
        action: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> AuditLogPage:
        limit = min(limit, MAX_PAGE_SIZE)

        query = select(AuditLog)
        count_query = select(func.count()).select_from(AuditLog)

        if entity_type is not None:
            query = query.where(AuditLog.entity_type == entity_type)
            count_query = count_query.where(AuditLog.entity_type == entity_type)
        if action is not None:
            query = query.where(AuditLog.action == action)
            count_query = count_query.where(AuditLog.action == action)
        if start_date is not None:
            start_dt = datetime.combine(start_date, time.min)
            query = query.where(AuditLog.created_at >= start_dt)
            count_query = count_query.where(AuditLog.created_at >= start_dt)
        if end_date is not None:
            end_dt = datetime.combine(end_date, time.max)
            query = query.where(AuditLog.created_at <= end_dt)
            count_query = count_query.where(AuditLog.created_at <= end_dt)

        total = await self.db.scalar(count_query) or 0

        # Newest first, tie-broken by id -- created_at alone can
        # collide at second-level precision under real usage; id is a
        # true, always-increasing tiebreaker for same-instant entries.
        query = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        entries = [AuditLogOut.model_validate(row) for row in result.scalars().all()]

        return AuditLogPage(entries=entries, total=total, limit=limit, offset=offset)
