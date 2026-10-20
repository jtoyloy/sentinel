from datetime import datetime
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.models import TradingEvent, EventOutbox
from app.models.schemas import TradingEventCreate, TradingEventResponse


class IngestionService:
    """Atomically persist events and their delivery intent; Redis is retried by the worker."""

    async def ingest_event(self, db: AsyncSession, event_data: TradingEventCreate):
        return (await self.ingest_batch(db, [event_data]))[0]

    async def ingest_batch(self, db: AsyncSession, events: List[TradingEventCreate], commit=True):
        results = []
        for data in events:
            existing = await db.get(TradingEvent, data.event_id) if data.event_id else None
            if existing:
                stored = TradingEventCreate(**{
                    **data.model_dump(),
                    **{k: getattr(existing, k) for k in data.model_fields if k not in ('metadata',)},
                    "metadata": existing.event_metadata,
                })
                if stored != data:
                    raise ValueError("event_id already exists with different content")
                results.append(existing)
                continue
            fields = data.model_dump(exclude={"metadata", "event_id"})
            event = TradingEvent(**fields, event_metadata=data.metadata)
            if data.event_id:
                event.event_id = data.event_id
            db.add(event)
            await db.flush()
            db.add(EventOutbox(event_id=event.event_id))
            results.append(event)
        await db.flush()
        if commit:
            await db.commit()
        return [TradingEventResponse.model_validate(e) for e in results]

    async def get_events(self, db, instrument_id=None, trader_id=None, from_time=None,
                         to_time=None, limit=100, offset=0):
        query = select(TradingEvent)
        for field, value in ((TradingEvent.instrument_id, instrument_id), (TradingEvent.trader_id, trader_id)):
            if value:
                query = query.where(field == value)
        if from_time:
            query = query.where(TradingEvent.timestamp >= from_time)
        if to_time:
            query = query.where(TradingEvent.timestamp <= to_time)
        result = await db.execute(query.order_by(TradingEvent.timestamp.desc(), TradingEvent.event_id).offset(offset).limit(limit))
        return [TradingEventResponse.model_validate(e) for e in result.scalars()]
