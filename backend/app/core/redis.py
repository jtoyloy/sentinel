import redis.asyncio as redis
from app.core.config import settings


class RedisClient:
    def __init__(self):
        self.client = None

    async def connect(self):
        self.client = redis.from_url(settings.REDIS_URL, decode_responses=True,
                                     socket_connect_timeout=2, socket_timeout=3)
        await self.client.ping()

    async def disconnect(self):
        if self.client:
            await self.client.aclose()

    async def publish_event(self, stream_name, data):
        if self.client is None:
            raise RuntimeError("Redis is not connected")
        return await self.client.xadd(stream_name, data)

    async def read_stream(self, stream_name, last_id="0-0", count=100):
        return await self.client.xread({stream_name: last_id}, count=count, block=1000)


redis_client = RedisClient()
