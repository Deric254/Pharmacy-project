"""
Waits for the configured database to accept connections, then exits
0. Used by docker-entrypoint.sh before running migrations -- the app
container can start before MySQL has finished initializing even with
a Docker healthcheck dependency, since 'healthy' only means the
mysqladmin ping succeeded, not that the specific database/user this
app connects as is fully ready for new connections yet.
"""

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

MAX_ATTEMPTS = 30
RETRY_DELAY_SECONDS = 2


async def wait_for_db() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                print("Database is ready.")
                return
            except Exception as exc:  # noqa: BLE001 -- deliberately broad, this is a readiness poll
                print(
                    f"  ...database not ready yet ({exc.__class__.__name__}), "
                    f"attempt {attempt}/{MAX_ATTEMPTS}",
                    file=sys.stderr,
                )
                await asyncio.sleep(RETRY_DELAY_SECONDS)
    finally:
        await engine.dispose()

    total_wait = MAX_ATTEMPTS * RETRY_DELAY_SECONDS
    print(f"Database never became ready after {total_wait}s.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(wait_for_db())
