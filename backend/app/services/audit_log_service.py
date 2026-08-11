from datetime import date, datetime, time
from typing import Any

from sqlalchemy import Select, func, select
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

    def _apply_filters(
        self,
        query: Select[Any],
        *,
        entity_type: str | None,
        action: str | None,
        start_date: date | None,
        end_date: date | None,
    ) -> Select[Any]:
        if entity_type is not None:
            query = query.where(AuditLog.entity_type == entity_type)
        if action is not None:
            query = query.where(AuditLog.action == action)
        if start_date is not None:
            query = query.where(AuditLog.created_at >= datetime.combine(start_date, time.min))
        if end_date is not None:
            query = query.where(AuditLog.created_at <= datetime.combine(end_date, time.max))
        return query

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

        query = self._apply_filters(
            select(AuditLog),
            entity_type=entity_type,
            action=action,
            start_date=start_date,
            end_date=end_date,
        )
        count_query = self._apply_filters(
            select(func.count()).select_from(AuditLog),
            entity_type=entity_type,
            action=action,
            start_date=start_date,
            end_date=end_date,
        )

        total = await self.db.scalar(count_query) or 0

        # Newest first, tie-broken by id -- created_at alone can
        # collide at second-level precision under real usage; id is a
        # true, always-increasing tiebreaker for same-instant entries.
        query = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        entries = [AuditLogOut.model_validate(row) for row in result.scalars().all()]

        return AuditLogPage(entries=entries, total=total, limit=limit, offset=offset)

    async def list_all_for_export(
        self,
        *,
        entity_type: str | None = None,
        action: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[AuditLogOut]:
        """
        Every matching row, not one page of them -- an export silently
        capped at the same 200-row page limit as the on-screen list
        would be a real accuracy gap, not a UI nicety.
        """
        query = self._apply_filters(
            select(AuditLog),
            entity_type=entity_type,
            action=action,
            start_date=start_date,
            end_date=end_date,
        ).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        result = await self.db.execute(query)
        return [AuditLogOut.model_validate(row) for row in result.scalars().all()]
