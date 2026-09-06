from datetime import date
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_time import local_day_bounds_utc
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

    async def _apply_filters(
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
        # Local midnight of each date, converted to the matching UTC
        # instant using THAT date's own DST/offset rule -- AuditLog.
        # created_at is stored in UTC (server_default=func.now()), so
        # the naive `datetime.combine(start_date, time.min)` this
        # replaced was comparing a local calendar date directly
        # against a UTC timestamp column: for a business ahead of UTC
        # (Kenya, UTC+3, is exactly this app's primary market), a
        # filter for "today" silently excluded the last few hours of
        # true local today and included the first few hours of true
        # local yesterday instead. sale_service.py and report_service.
        # py both already use this same local_day_bounds_utc helper for
        # exactly this reason -- this was the one remaining place still
        # doing the naive comparison it exists to replace.
        if start_date is not None:
            utc_start, _ = await local_day_bounds_utc(self.db, start_date)
            query = query.where(AuditLog.created_at >= utc_start)
        if end_date is not None:
            _, utc_end_exclusive = await local_day_bounds_utc(self.db, end_date)
            query = query.where(AuditLog.created_at < utc_end_exclusive)
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

        query = await self._apply_filters(
            select(AuditLog),
            entity_type=entity_type,
            action=action,
            start_date=start_date,
            end_date=end_date,
        )
        count_query = await self._apply_filters(
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
        query = await self._apply_filters(
            select(AuditLog),
            entity_type=entity_type,
            action=action,
            start_date=start_date,
            end_date=end_date,
        )
        query = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        result = await self.db.execute(query)
        return [AuditLogOut.model_validate(row) for row in result.scalars().all()]
