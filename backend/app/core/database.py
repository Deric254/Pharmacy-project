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
def _configure_sqlite_pragmas(dbapi_connection: Any, _connection_record: object) -> None:
    """
    Every PRAGMA here is per-connection (SQLite doesn't persist these
    in the database file itself, except journal_mode, which is
    idempotent to re-set), so this runs on every new connection, not
    once at startup.

    - foreign_keys=ON: SQLite does not enforce foreign key constraints
      by default -- a real, permanent gap now that SQLite is this
      app's only backend (previously accepted because MySQL, the
      production target, always enforced them). Without this, the
      database would silently accept e.g. a stock batch pointing at a
      nonexistent product, relying entirely on application-level
      checks with no database-level backstop. dump_restore.py
      explicitly toggles this OFF for the duration of a restore
      (deleting and reinserting every table necessarily violates FKs
      transiently); _enforce_foreign_keys_on_checkout below is what
      guarantees it is back ON afterward.

    - journal_mode=WAL: this app's real scenario is a handful of
      concurrent users on one machine, not a single writer. WAL mode
      is what actually matters there -- readers no longer block
      writers and writers no longer block readers, leaving only
      writer-vs-writer contention, which busy_timeout above already
      handles by making the second writer wait instead of erroring.
      The default rollback-journal mode blocks readers during any
      write, which is the wrong tradeoff for this app's actual usage.

    - synchronous=FULL: the setting most tempting to skip for a little
      extra write throughput. Costs real performance; buys a genuine
      guarantee that a committed transaction survives actual power
      loss, not just a process crash (a crashed process's writes are
      already safe via the OS page cache either way -- this is
      specifically about the machine itself losing power mid-write,
      a real possibility for a desktop app that isn't necessarily
      running behind a UPS). Given this app's standing bar -- a
      recorded sale must never be lost -- this is worth paying for.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=FULL")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency -- one session per request, always closed."""
    async with AsyncSessionLocal() as session:
        yield session


@event.listens_for(engine.sync_engine, "checkout")
def _enforce_foreign_keys_on_checkout(
    dbapi_connection: Any, _connection_record: object, _connection_proxy: object
) -> None:
    """
    foreign_keys is per-connection, SQLite silently ignores changes to it
    while a transaction is open, and a Session does not keep one
    connection across commits. So code that switches it off for a single
    transaction (dump_restore.py) cannot reliably switch it back on
    afterwards: the follow-up PRAGMA lands on whichever pooled connection
    the session happens to check out next, while the connection that was
    switched off returns to the pool still off. Re-asserting it every
    time a connection leaves the pool -- before anything can have opened
    a transaction on it -- means no connection is ever put back in
    service with enforcement off, however it was left.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
