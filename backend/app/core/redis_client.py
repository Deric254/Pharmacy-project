"""
Single shared Redis client. Both the event bus (pub/sub) and any
cache-aside reads (business config, reports) go through this one
connection pool instead of each module opening its own.
"""

import redis.asyncio as redis

from app.core.config import get_settings

settings = get_settings()

redis_client: redis.Redis = redis.from_url(settings.redis_url, decode_responses=True)
