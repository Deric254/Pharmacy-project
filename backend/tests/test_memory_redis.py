"""
In-memory Redis fallback tests. This is what makes the desktop .exe
able to run without a separately-installed Redis server -- these
tests prove the fake actually behaves like Redis for the operations
the app uses, not just that it has matching method names.
"""

import asyncio

import pytest

from app.core.memory_redis import InMemoryRedisClient


@pytest.fixture
def client() -> InMemoryRedisClient:
    return InMemoryRedisClient()


class TestBasicOps:
    async def test_get_on_missing_key_returns_none(self, client):
        assert await client.get("nope") is None

    async def test_set_then_get_round_trips(self, client):
        await client.set("key", "value")
        assert await client.get("key") == "value"

    async def test_delete_removes_the_key(self, client):
        await client.set("key", "value")
        await client.delete("key")
        assert await client.get("key") is None

    async def test_delete_on_missing_key_does_not_raise(self, client):
        await client.delete("never-existed")  # must not raise

    async def test_flushdb_clears_everything(self, client):
        await client.set("a", "1")
        await client.set("b", "2")
        await client.flushdb()
        assert await client.get("a") is None
        assert await client.get("b") is None


class TestExpiry:
    async def test_set_with_ex_expires_the_key(self, client):
        await client.set("key", "value", ex=0)  # already-expired by the time we check
        await asyncio.sleep(0.01)
        assert await client.get("key") is None

    async def test_set_without_ex_never_expires(self, client):
        await client.set("key", "value")
        await asyncio.sleep(0.01)
        assert await client.get("key") == "value"

    async def test_expire_sets_a_ttl_on_an_existing_key(self, client):
        await client.set("key", "value")
        await client.expire("key", 0)
        await asyncio.sleep(0.01)
        assert await client.get("key") is None

    async def test_expire_on_a_missing_key_does_not_raise(self, client):
        await client.expire("never-existed", 60)  # must not raise, just a no-op


class TestIncr:
    async def test_incr_on_missing_key_starts_at_one(self, client):
        assert await client.incr("counter") == 1

    async def test_incr_increments_an_existing_value(self, client):
        await client.set("counter", "5")
        assert await client.incr("counter") == 6

    async def test_incr_does_not_reset_an_existing_ttl(self, client):
        """
        This is the exact semantic the login rate limiter depends on:
        expire() is only called once, on the FIRST failure in a
        window -- every subsequent incr() during that window must
        preserve the original deadline, not extend or clear it.
        """
        await client.set("counter", "1", ex=100)
        await client.incr("counter")
        await client.incr("counter")
        # Still has an expiry (would be None if incr() had cleared it).
        entry = client._store["counter"]
        assert entry.expires_at is not None


class TestPubSub:
    async def test_a_published_message_reaches_a_subscriber(self, client):
        pubsub = client.pubsub()
        await pubsub.subscribe("test-channel")

        await client.publish("test-channel", "hello")

        messages = []
        async for message in pubsub.listen():
            messages.append(message)
            if message["type"] == "message":
                break

        real_messages = [m for m in messages if m["type"] == "message"]
        assert len(real_messages) == 1
        assert real_messages[0]["channel"] == "test-channel"
        assert real_messages[0]["data"] == "hello"

    async def test_a_message_on_a_different_channel_is_not_delivered(self, client):
        pubsub = client.pubsub()
        await pubsub.subscribe("channel-a")

        await client.publish("channel-b", "should not arrive")
        await client.publish("channel-a", "should arrive")

        real_message = None
        async for message in pubsub.listen():
            if message["type"] == "message":
                real_message = message
                break

        assert real_message["data"] == "should arrive"

    async def test_unsubscribe_stops_further_delivery(self, client):
        pubsub = client.pubsub()
        await pubsub.subscribe("channel")
        await pubsub.unsubscribe()

        await client.publish("channel", "nobody should get this")

        # No listener is pulled here on purpose -- proving delivery
        # stopped means proving the queue stays empty, which we check
        # indirectly: the subscriber list for the channel no longer
        # contains this pubsub's queue.
        assert len(client._subscribers.get("channel", [])) == 0

    async def test_two_subscribers_both_receive_the_same_publish(self, client):
        pubsub_a = client.pubsub()
        pubsub_b = client.pubsub()
        await pubsub_a.subscribe("shared")
        await pubsub_b.subscribe("shared")

        await client.publish("shared", "broadcast")

        async def first_real_message(pubsub):
            async for message in pubsub.listen():
                if message["type"] == "message":
                    return message
            return None

        result_a, result_b = await asyncio.gather(
            first_real_message(pubsub_a), first_real_message(pubsub_b)
        )
        assert result_a["data"] == "broadcast"
        assert result_b["data"] == "broadcast"
