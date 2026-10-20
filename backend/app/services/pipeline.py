"""Durable at-least-once outbox delivery and event-time stream processing.

Run a single embedded worker per deployment. The PostgreSQL advisory lock
ensures multiple API processes cannot advance the shared cursor concurrently.
"""
import asyncio
import logging
from datetime import timedelta, datetime, timezone
from uuid import UUID
from sqlalchemy import select, text
from app.core.database import async_session_maker, engine
from app.core.redis import redis_client
from app.models.models import EventOutbox, TradingEvent, StreamCursor
from app.services.detection import DetectionEngine
from app.services.alerting import AlertingService

logger = logging.getLogger(__name__)
STREAM = "sentinel:trading_events"


async def publish_outbox(db):
    rows = (await db.scalars(select(EventOutbox).where(
        EventOutbox.published.is_(False)).order_by(EventOutbox.id).limit(250))).all()
    for row in rows:
        await redis_client.publish_event(STREAM, {"event_id": str(row.event_id)})
        row.published = True
    await db.commit()
    return len(rows)


async def process_event(db, event_id):
    event = await db.get(TradingEvent, UUID(str(event_id)))
    if event is None:
        raise ValueError("Stream references missing persisted event")
    end = event.timestamp + timedelta(seconds=90)
    detections = await DetectionEngine().process_detection_window(
        db, event.instrument_id, window_minutes=3, end_time=end)
    created = []
    service = AlertingService()
    for detection in detections:
        alert = await service.create_alert_from_detection(db, detection)
        if alert:
            created.append(alert)
    if event.event_type == "EXECUTION":
        from app.domains.communications import correlate_event
        from app.platform import serialize
        from fastapi.encoders import jsonable_encoder
        for signal in await correlate_event(db, event):
            created.append({"type": "SIGNAL", **jsonable_encoder(serialize(signal))})
    return created


async def consume_once(db, broadcast):
    cursor = await db.get(StreamCursor, "detection-v1")
    if cursor is None:
        cursor = StreamCursor(name="detection-v1", last_id="0-0")
        db.add(cursor)
        await db.commit()
    batches = await redis_client.read_stream(STREAM, cursor.last_id)
    count = 0
    for _, messages in batches:
        for stream_id, payload in messages:
            try:
                alerts = await process_event(db, payload.get("event_id"))
            except (ValueError, TypeError) as error:
                # Invalid stream references are preserved and retried, then quarantined.
                # Dependency/database errors are not quarantined and never advance cursor.
                from app.platform import StreamFailure
                await db.rollback()
                cursor = await db.get(StreamCursor, "detection-v1")
                failure = await db.scalar(select(StreamFailure).where(StreamFailure.stream_id == stream_id))
                if failure:
                    failure.attempts += 1
                else:
                    failure = StreamFailure(stream_id=stream_id, payload=payload,
                                            error=type(error).__name__, attempts=1)
                    db.add(failure)
                if failure.attempts < 3:
                    await db.commit()
                    raise ValueError("Invalid stream reference retained for retry") from error
                failure.status = "QUARANTINED"
                alerts = []
            cursor.last_id = stream_id
            # Alerts and cursor advance atomically. Repeat delivery is deduplicated.
            await db.commit()
            for alert in alerts:
                await broadcast(alert if isinstance(alert, dict) else {"type": "ALERT", **alert.model_dump(mode="json")})
            count += 1
    return count


async def run_pipeline(broadcast):
    while True:
        try:
            # Session-level lock is released even if the worker crashes.
            async with engine.connect() as lock_conn:
                locked = True
                if engine.dialect.name == "postgresql":
                    locked = await lock_conn.scalar(text("SELECT pg_try_advisory_lock(73921811)"))
                if not locked:
                    await asyncio.sleep(1)
                    continue
                try:
                    while True:
                        async with async_session_maker() as db:
                            await publish_outbox(db)
                            processed = await consume_once(db, broadcast)
                            from app.platform import WorkerHeartbeat
                            heartbeat = await db.get(WorkerHeartbeat, "detection-v1")
                            if heartbeat is None:
                                heartbeat = WorkerHeartbeat(name="detection-v1", processed=0, failures=0)
                                db.add(heartbeat)
                            heartbeat.last_success = datetime.now(timezone.utc)
                            heartbeat.processed += processed
                            await db.commit()
                        await asyncio.sleep(0.05)
                finally:
                    if engine.dialect.name == "postgresql":
                        await lock_conn.execute(text("SELECT pg_advisory_unlock(73921811)"))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            try:
                from app.platform import WorkerHeartbeat
                async with async_session_maker() as db:
                    heartbeat = await db.get(WorkerHeartbeat, "detection-v1")
                    if heartbeat is None:
                        heartbeat = WorkerHeartbeat(name="detection-v1", processed=0, failures=0)
                        db.add(heartbeat)
                    heartbeat.last_failure = datetime.now(timezone.utc)
                    heartbeat.failures += 1
                    heartbeat.last_error = type(error).__name__
                    await db.commit()
            except Exception:
                logger.error("Could not persist worker failure heartbeat")
            logger.exception("Pipeline delivery failed; retrying persisted work")
            await asyncio.sleep(1)
