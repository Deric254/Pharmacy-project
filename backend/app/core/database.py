"""
Database engine and session management.

Single engine, async throughout (matches FastAPI's async request model --
mixing sync DB calls into an async app is a classic source of blocked
event-loop slowdowns, so we avoid it from day one).

Why `engine` and `AsyncSessionLocal` are loop-aware proxies rather than
plain module-level objects: a real async DB driver (asyncmy for MySQL,
same as redis.asyncio for Redis -- see redis_client.py's docstring for
the fuller explanation) opens its connection bound to whichever event
loop is running the moment it first connects. A module-level engine
constructed once at import time works fine in production, where a
process runs exactly one event loop for its whole life, but breaks
anywhere a new event loop can appear later -- a test suite that
creates one event loop per test being the obvious case.

This was never caught by the test suite because every test run before
now used SQLite (aiosqlite), whose driver happens not to hit this
failure mode. The instant real MySQL was used for the first time (a
genuine TCP-based async driver, like Redis), every single test failed
with `RuntimeError: ... attached to a different loop` -- confirmed by
actually doing it, not assumed. Given ci.yml's test job targets real
MySQL, this means the database layer of that CI job may have been
failing this whole time, silently, in an environment nobody running
this codebase against SQLite could ever have observed.
"""

import asyncio
import os
from collections.abc import AsyncGenerator
from typing import Any, cast

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()

_engines_by_loop: dict[int, AsyncEngine] = {}
_sessionmakers_by_loop: dict[int, async_sessionmaker[AsyncSession]] = {}


def _running_under_pytest() -> bool:
    # Set automatically by pytest for the duration of every test --
    # no test-side configuration needed, and it's unset again the
    # moment the test session ends, so this can't leak into any real
    # deployment by accident.
    return "PYTEST_CURRENT_TEST" in os.environ


def _get_engine() -> AsyncEngine:
    loop = asyncio.get_running_loop()
    key = id(loop)
    eng = _engines_by_loop.get(key)
    if eng is None:
        if _running_under_pytest():
            # No connection is ever held open between operations, so
            # there's nothing for a disposed-but-not-yet-torn-down
            # connection to collide with when the next test's engine
            # opens its own. Trades a little per-query latency (a
            # fresh TCP handshake every time) for genuinely bounded
            # connection usage across hundreds of short-lived engines
            # -- a trade only worth making in exactly this situation.
            eng = create_async_engine(
                settings.database_url,
                poolclass=NullPool,
                echo=settings.environment == "development",
            )
        else:
            eng = create_async_engine(
                settings.database_url,
                # Sized for this app's actual scale (a single
                # pharmacy, a handful of concurrent staff), not an
                # arbitrary default.
                pool_size=3,
                max_overflow=2,
                pool_timeout=10,  # fail fast and loud, not a long silent hang
                pool_pre_ping=True,  # avoids "server has gone away" on idle connections
                echo=settings.environment == "development",
            )
        _engines_by_loop[key] = eng
    return eng


def _get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    loop = asyncio.get_running_loop()
    key = id(loop)
    maker = _sessionmakers_by_loop.get(key)
    if maker is None:
        maker = async_sessionmaker(_get_engine(), expire_on_commit=False, class_=AsyncSession)
        _sessionmakers_by_loop[key] = maker
    return maker


class _EngineProxy:
    """Forwards all attribute access to the per-event-loop engine."""

    def __getattr__(self, name: str) -> Any:
        return getattr(_get_engine(), name)


class _SessionmakerProxy:
    """Forwards calls (`AsyncSessionLocal()`) to the per-event-loop sessionmaker."""

    def __call__(self, *args: Any, **kwargs: Any) -> AsyncSession:
        return _get_sessionmaker()(*args, **kwargs)


engine: AsyncEngine = cast("AsyncEngine", _EngineProxy())
AsyncSessionLocal: async_sessionmaker[AsyncSession] = cast(
    "async_sessionmaker[AsyncSession]", _SessionmakerProxy()
)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency -- one session per request, always closed."""
    async with AsyncSessionLocal() as session:
        yield session


async def dispose_engine_for_current_loop() -> None:
    """
    Dispose and evict the engine (and its connection pool) belonging
    to the currently running event loop, if one was created.

    Must be called before that loop closes, or every connection in the
    pool is leaked. The app's own lifespan calls this on shutdown; a
    test suite that spins up one event loop per test must call this in
    its per-test teardown -- exactly the same requirement
    redis_client.py's aclose_for_current_loop() documents, for the
    same reason.
    """
    loop = asyncio.get_running_loop()
    key = id(loop)
    eng = _engines_by_loop.pop(key, None)
    _sessionmakers_by_loop.pop(key, None)
    if eng is not None:
        await eng.dispose()
