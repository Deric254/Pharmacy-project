"""
Local-date <-> UTC-instant conversion, shared by every service that
lets an owner pick a date range ("today", "this week") and needs to
filter rows whose timestamp columns are stored in UTC.

Every timestamp column written via SQLAlchemy's `func.now()` against
this app's SQLite database is UTC (SQLite's CURRENT_TIMESTAMP). Every
date range a person types into a report or sales-history filter is a
LOCAL calendar date -- the date on their wall calendar, per
BusinessConfig.timezone (default Africa/Nairobi). Comparing a UTC
timestamp's date component directly against that local date silently
misclassifies any row whose local time and UTC time fall on different
calendar days -- proven directly: a sale at 01:00 Africa/Nairobi time
(UTC+3) is stored as 22:00 UTC the day before, and a naive
`func.date(created_at) >= start_date` filter drops it from "today"
entirely, with no error, just a report that's quietly wrong.

BusinessConfigService.get() is Redis-cached, so calling this from
several services per request is cheap -- it does not re-hit the DB
on every call.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.business_config_service import BusinessConfigService


async def get_utc_offset_minutes(db: AsyncSession) -> int:
    """
    The business's current UTC offset in minutes, derived from its
    configured IANA timezone at "now". A single evaluation per call
    is exact for fixed-offset zones (Africa/Nairobi has no DST) and
    correct for the overwhelming majority of report ranges even in a
    DST-observing zone -- the one residual edge case is a range that
    straddles the precise moment of a DST transition, which is still
    a far smaller error than the complete absence of timezone
    handling this replaces.
    """
    config = await BusinessConfigService(db).get()
    try:
        tz = ZoneInfo(config.timezone)
    except Exception:  # noqa: BLE001 - a bad saved timezone name must never break a report
        tz = ZoneInfo("UTC")
    offset = datetime.now(tz).utcoffset() or timedelta(0)
    return int(offset.total_seconds() // 60)


async def local_day_bounds_utc(
    db: AsyncSession, start_date: date, end_date: date | None = None
) -> tuple[datetime, datetime]:
    """
    Converts a LOCAL calendar-date range into the [start, end) UTC
    instant range that actually matches how timestamp columns are
    stored -- so a plain `column >= start AND column < end` replaces
    any `func.date(column)` string comparison. When end_date is None,
    returns the bounds for the single day start_date.
    """
    offset = await get_utc_offset_minutes(db)
    end_date = end_date if end_date is not None else start_date
    local_start = datetime.combine(start_date, time.min)
    local_end_exclusive = datetime.combine(end_date + timedelta(days=1), time.min)
    return (
        local_start - timedelta(minutes=offset),
        local_end_exclusive - timedelta(minutes=offset),
    )
