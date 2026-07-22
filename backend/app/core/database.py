"""
Database engine and session management.

Single engine, async throughout (matches FastAPI's async request model --
mixing sync DB calls into an async app is a classic source of blocked
event-loop slowdowns, so we avoid it from day one).

This used to need a much more complicated per-event-loop engine proxy,
specifically to work around a real bug: a genuine TCP-based async
driver (MySQL's asyncmy, back when this app supported MySQL) opens its
connection bound to whichever event loop is running the moment it
first connects, which breaks the instant a second event loop appears
(a test suite creating one per test, for example). aiosqlite -- the
only driver this app uses now -- does not have that failure mode, so
none of that machinery is needed anymore. This is deliberately back to
the plain, obvious version.
"""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.environment == "development",
    # SQLite's busy_timeout (seconds to wait for a locked database
    # before raising, rather than failing instantly). Without this, a
    # second writer arriving while the first is still mid-transaction
    # gets an immediate "database is locked" error instead of waiting
    # its turn -- confirmed directly: two overlapping write
    # transactions against a real SQLite file, no timeout configured,
    # the second one failed in 0.00s; with this set, it waited for the
    # first to finish and then succeeded. 5s is comfortably longer
    # than any single request in this app takes.
    connect_args={"timeout": 5},
)


@event.listens_for(engine.sync_engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: object) -> None:
    """
    SQLite does not enforce foreign key constraints by default -- a
    real, permanent gap now that SQLite is this app's only backend
    (previously accepted because MySQL, the production target, always
    enforced them). Without this, the database would silently accept
    e.g. a stock batch pointing at a nonexistent product, relying
    entirely on application-level checks with no database-level
    backstop. Set on every new connection, since SQLite's PRAGMAs are
    per-connection, not persistent in the database file itself.

    dump_restore.py explicitly toggles this OFF for the duration of a
    restore (deleting and reinserting every table necessarily violates
    FKs transiently) and back ON afterward -- that toggle only matters
    at all because this is on by default everywhere else.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency -- one session per request, always closed."""
    async with AsyncSessionLocal() as session:
        yield session
