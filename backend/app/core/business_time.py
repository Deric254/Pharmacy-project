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

Every conversion here resolves each date's OWN UTC offset via
astimezone() -- never a single offset computed once "as of now" and
applied everywhere. That distinction only matters for timezones that
observe DST (Africa/Nairobi never has, so this app's own default
deployment was never at risk), but this software is built to be sold
to pharmacies anywhere: a sale made in July, checked in a report run
in January, needs July's offset applied to July's date, not January's
-- proven directly: for America/New_York, a sale at 00:30 local time
on July 15th (EDT, UTC-4) is correctly bucketed as July 15th using
July's own offset, but silently misclassified as July 14th if a
January-run report applies January's offset (EST, UTC-5) instead.

BusinessConfigService.get() is Redis-cached, so calling this from
several services per request is cheap -- it does not re-hit the DB
on every call.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.business_config_service import BusinessConfigService


async def get_business_timezone(db: AsyncSession) -> ZoneInfo:
    """
    The business's configured IANA timezone, falling back to UTC for
    a bad/unset saved name -- a typo'd timezone string must never
    break a report.
    """
    config = await BusinessConfigService(db).get()
    try:
        return ZoneInfo(config.timezone)
    except Exception:  # noqa: BLE001 - a bad saved timezone name must never break a report
        return ZoneInfo("UTC")


async def business_today(db: AsyncSession) -> date:
    """
    "Today", as a calendar date, in the business's configured
    timezone -- never the server process's own OS clock.

    The bug this closes: `date.today()` answers "what day is it
    where this process happens to be running", not "what day is it
    for this business". Those match for a single-location desktop
    install running on a clock set to the same timezone as the shop
    -- but this app is sold to be run anywhere (see this module's
    own docstring), including from a cloud-hosted backend that
    defaults to UTC, or checked from a device in a different
    timezone than the business itself. Whenever those two clocks
    disagree, every caller that used to do `date.today()` directly
    would silently drop or shift the last few hours of a business
    day out of "today" -- proven directly: a real sale, made and
    committed a moment before the call, went missing from that same
    moment's "today" revenue figure, with no error raised anywhere.

    Every place in this codebase that means "today, for this
    business" -- not "today, on this machine" -- must resolve it
    through this function, so there is exactly one definition of
    "today" and it is always correct regardless of where the app or
    the person asking happens to be.
    """
    tz = await get_business_timezone(db)
    return datetime.now(tz).date()


def _utc_instant_for_local_midnight(tz: ZoneInfo, local_date: date) -> datetime:
    """
    The UTC instant corresponding to local midnight on `local_date`,
    resolved using THAT date's own DST rule via astimezone() -- not a
    general "current offset". This is what makes date-boundary math
    correct year-round, not just at the moment a report happens to be
    run.
    """
    local_midnight = datetime.combine(local_date, time.min, tzinfo=tz)
    return local_midnight.astimezone(UTC).replace(tzinfo=None)


async def local_day_bounds_utc(
    db: AsyncSession, start_date: date, end_date: date | None = None
) -> tuple[datetime, datetime]:
    """
    Converts a LOCAL calendar-date range into the [start, end) UTC
    instant range that actually matches how timestamp columns are
    stored -- so a plain `column >= start AND column < end` replaces
    any `func.date(column)` string comparison. When end_date is None,
    returns the bounds for the single day start_date. Each boundary
    is resolved against its own date, so this stays correct even when
    the report is run long after the date it's asking about.
    """
    tz = await get_business_timezone(db)
    end_date = end_date if end_date is not None else start_date
    utc_start = _utc_instant_for_local_midnight(tz, start_date)
    utc_end_exclusive = _utc_instant_for_local_midnight(tz, end_date + timedelta(days=1))
    return (utc_start, utc_end_exclusive)


def _offset_minutes_for_date(tz: ZoneInfo, local_date: date) -> int:
    offset = datetime.combine(local_date, time.min, tzinfo=tz).utcoffset() or timedelta(0)
    return int(offset.total_seconds() // 60)


async def local_offset_segments(
    db: AsyncSession, start_date: date, end_date: date
) -> list[tuple[date, date, int]]:
    """
    Splits [start_date, end_date] into contiguous sub-ranges where the
    business's UTC offset is constant, each tagged with that offset in
    minutes (local midnight's offset, matching how day boundaries are
    computed elsewhere in this module).

    Exists for SQL-side GROUP BY bucketing (revenue trend: grouping
    thousands of rows into calendar days/weeks/months entirely in the
    database, not row-by-row in Python, so it stays fast at any data
    volume). A single offset cannot correctly bucket rows that span a
    DST transition -- Africa/Nairobi never has one, so this was never
    externally visible in this app's own deployment, but a
    DST-observing client's multi-month or multi-year chart routinely
    would span at least one. Each segment gets queried with its own
    correct offset and the results are summed by period label, which
    is safe because revenue/count/cost are all purely additive.
    """
    tz = await get_business_timezone(db)
    total_days = (end_date - start_date).days + 1
    segments: list[tuple[date, date, int]] = []
    segment_start = start_date
    segment_offset = _offset_minutes_for_date(tz, start_date)
    for day_index in range(1, total_days):
        current_date = start_date + timedelta(days=day_index)
        offset = _offset_minutes_for_date(tz, current_date)
        if offset != segment_offset:
            segments.append((segment_start, current_date - timedelta(days=1), segment_offset))
            segment_start = current_date
            segment_offset = offset
    segments.append((segment_start, end_date, segment_offset))
    return segments
