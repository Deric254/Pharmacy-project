"""
Single shared Redis client. Both the event bus (pub/sub) and any
cache-aside reads (business config, reports) go through this one
connection pool instead of each module opening its own.

Why this is a proxy and not a plain module-level client:
redis-py's async client opens its socket lazily, bound to whichever
event loop is running at that moment. A client constructed once at
import time (before any event loop exists) and then reused works fine
in production, where a process runs exactly one event loop for its
whole life -- but breaks anywhere a new event loop can appear later
(most notably: a test suite that creates one event loop per test).
Reusing a connection bound to an already-closed loop raises
"got Future ... attached to a different loop" / "Event loop is
closed".

`_RedisClientProxy` defers construction until first use and keys the
underlying client by the currently running event loop, so each loop
transparently gets its own connection pool. Existing call sites
(`redis_client.get(...)`, `redis_client.publish(...)`, etc.) are
unaffected since attribute access is forwarded via `__getattr__`.
"""

import asyncio
from typing import Any, cast

import redis.asyncio as redis

from app.core.config import get_settings

settings = get_settings()

_clients_by_loop: dict[int, redis.Redis] = {}


def _get_client() -> redis.Redis:
    loop = asyncio.get_running_loop()
    key = id(loop)
    client = _clients_by_loop.get(key)
    if client is None:
        client = cast(
            "redis.Redis",
            redis.from_url(settings.redis_url, decode_responses=True),  # type: ignore[no-untyped-call]
        )
        _clients_by_loop[key] = client
    return client


class _RedisClientProxy:
    """Forwards all attribute access to the per-event-loop Redis client."""

    def __getattr__(self, name: str) -> Any:
        return getattr(_get_client(), name)


redis_client: redis.Redis = cast("redis.Redis", _RedisClientProxy())


async def aclose_for_current_loop() -> None:
    """
    Close and evict the Redis client belonging to the currently running
    event loop, if one was created.

    Must be called before that loop closes, or the connection is
    leaked (the socket is never released and the entry stays in
    `_clients_by_loop` forever, keyed by a since-freed loop id that
    could theoretically collide with a future loop). The app's own
    lifespan calls this on shutdown; a test suite that spins up one
    event loop per test must call this in its per-test teardown.
    """
    loop = asyncio.get_running_loop()
    key = id(loop)
    client = _clients_by_loop.pop(key, None)
    if client is not None:
        await client.aclose()
