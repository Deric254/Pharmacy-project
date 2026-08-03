"""
In-memory stand-in for Redis, used only in desktop single-process mode
(REDIS_MODE=memory). A bundled desktop .exe can't reasonably require
the person running it to separately install and start a real Redis
server -- that's not "clean and ready for work." But the app's actual
use of Redis is narrow (rate-limit counters, a cache-aside read, and
a pub/sub event bus), all of which only ever need to work within a
single process's lifetime for a single-user desktop deployment. This
implements exactly that subset, verified against every real call site
in the app (grep -rn "redis_client\\." app/), not a general Redis
clone.

Server/Docker deployments never use this -- they keep talking to a
real Redis via REDIS_URL, unchanged. This only activates when
REDIS_MODE=memory, which only the desktop launcher sets.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class _Entry:
    value: str
    expires_at: float | None  # monotonic time.time() deadline, or None = never


class InMemoryPubSub:
    """Matches the subset of redis-py's PubSub interface events.py and
    notification_dispatcher.py actually use: subscribe, unsubscribe,
    and an async `listen()` generator yielding {"type", "channel",
    "data"} dicts -- the same shape a real Redis pubsub message has."""

    def __init__(self, hub: InMemoryRedisClient) -> None:
        self._hub = hub
        self._queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self._channels: set[str] = set()

    async def subscribe(self, channel: str) -> None:
        self._channels.add(channel)
        self._hub._register_subscriber(channel, self._queue)
        # Real redis-py emits a "subscribe" confirmation message first;
        # dispatch_one_message() ignores anything whose type isn't
        # "message", so this is here for shape-fidelity, not behavior.
        await self._queue.put({"type": "subscribe", "channel": channel, "data": 1})

    async def unsubscribe(self, *channels: str) -> None:
        # Matches real redis-py: no channels given unsubscribes from
        # everything this pubsub is currently on; specific channel
        # names unsubscribe only those.
        targets = list(channels) if channels else list(self._channels)
        for channel in targets:
            self._hub._unregister_subscriber(channel, self._queue)
            self._channels.discard(channel)

    async def listen(self) -> AsyncIterator[dict[str, object]]:
        while True:
            message = await self._queue.get()
            yield message

    async def get_message(
        self, ignore_subscribe_messages: bool = False, timeout: float = 0.0
    ) -> dict[str, object] | None:
        """
        Matches real redis-py's PubSub.get_message(): waits up to
        `timeout` seconds for the next message, returning None on
        timeout rather than blocking forever. Needed alongside listen()
        because some call sites poll for one message at a time (e.g.
        tests asserting a specific event fired) instead of iterating
        an unbounded stream.
        """
        while True:
            try:
                message = await asyncio.wait_for(self._queue.get(), timeout=timeout or None)
            except TimeoutError:
                return None
            if ignore_subscribe_messages and message.get("type") == "subscribe":
                continue
            return message


@dataclass
class InMemoryRedisClient:
    _store: dict[str, _Entry] = field(default_factory=dict)
    _subscribers: dict[str, list[asyncio.Queue[dict[str, object]]]] = field(default_factory=dict)

    def _evict_if_expired(self, key: str) -> None:
        entry = self._store.get(key)
        if entry is not None and entry.expires_at is not None and entry.expires_at <= time.time():
            del self._store[key]

    async def get(self, key: str) -> str | None:
        self._evict_if_expired(key)
        entry = self._store.get(key)
        return entry.value if entry is not None else None

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        expires_at = time.time() + ex if ex is not None else None
        self._store[key] = _Entry(value=str(value), expires_at=expires_at)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def incr(self, key: str) -> int:
        self._evict_if_expired(key)
        entry = self._store.get(key)
        current = int(entry.value) if entry is not None else 0
        new_value = current + 1
        # incr() must NOT reset an existing TTL (that's expire()'s job,
        # called separately by the rate limiter only on the first
        # attempt in a window) -- preserve it if this key already had one.
        expires_at = entry.expires_at if entry is not None else None
        self._store[key] = _Entry(value=str(new_value), expires_at=expires_at)
        return new_value

    async def expire(self, key: str, seconds: int) -> None:
        entry = self._store.get(key)
        if entry is not None:
            entry.expires_at = time.time() + seconds

    async def flushdb(self) -> None:
        self._store.clear()
        self._subscribers.clear()

    async def publish(self, channel: str, message: str) -> None:
        for queue in self._subscribers.get(channel, []):
            await queue.put({"type": "message", "channel": channel, "data": message})

    def pubsub(self) -> InMemoryPubSub:
        return InMemoryPubSub(self)

    def _register_subscriber(self, channel: str, queue: asyncio.Queue[dict[str, object]]) -> None:
        self._subscribers.setdefault(channel, []).append(queue)

    def _unregister_subscriber(self, channel: str, queue: asyncio.Queue[dict[str, object]]) -> None:
        subscribers = self._subscribers.get(channel, [])
        if queue in subscribers:
            subscribers.remove(queue)


# One process, one store, for the lifetime of the desktop app -- unlike
# the real-Redis path, there's no per-event-loop complexity to manage
# here (see redis_client.py's docstring for why that's necessary
# there): this is pure Python state, never a socket, so it's already
# safe to share across whatever event loop happens to be running.
_singleton = InMemoryRedisClient()


def get_in_memory_redis_client() -> InMemoryRedisClient:
    return _singleton
