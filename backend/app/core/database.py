"""
Database engine and session management.

Single engine, async throughout (matches FastAPI's async request model —
mixing sync DB calls into an async app is a classic source of blocked
event-loop slowdowns, so we avoid it from day one).
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # avoids "server has gone away" errors on idle connections
    echo=settings.environment == "development",
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — one session per request, always closed."""
    async with AsyncSessionLocal() as session:
        yield session
