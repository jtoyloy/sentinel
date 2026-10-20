from datetime import datetime
from uuid import UUID
from typing import List, Optional, Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from app.core.database import get_db
from app.core.security import require_reader, require_analyst
from app.models.schemas import TradingEventCreate, TradingEventResponse
from app.services.ingestion import IngestionService

router = APIRouter(prefix="/api/events", tags=["events"], dependencies=[Depends(require_reader)])
service = IngestionService()


async def store(db, events):
    for attempt in range(2):
        try:
            return await service.ingest_batch(db, events)
        except ValueError:
            await db.rollback()
            raise HTTPException(409, "Event identifier conflicts with existing content")
        except IntegrityError:
            await db.rollback()
            # Concurrent identical UUID deliveries can race the initial lookup.
            # Re-read after the winning transaction commits; compare exact content.
            if attempt:
                raise HTTPException(409, "Event identifier conflicts with existing content")



@router.post("/ingest", response_model=TradingEventResponse, dependencies=[Depends(require_analyst)])
async def ingest_event(event: TradingEventCreate, db: AsyncSession = Depends(get_db)):
    return (await store(db, [event]))[0]


@router.post("/ingest/batch", response_model=List[TradingEventResponse], dependencies=[Depends(require_analyst)])
async def ingest_batch(events: Annotated[List[TradingEventCreate], Body(min_length=1, max_length=1000)],
                       db: AsyncSession = Depends(get_db)):
    return await store(db, events)


@router.get("", response_model=List[TradingEventResponse])
async def get_events(instrument_id: Optional[str] = None, trader_id: Optional[str] = None,
                     from_time: Optional[datetime] = None, to_time: Optional[datetime] = None,
                     limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), replay_run_id: Optional[UUID] = None,
                     db: AsyncSession = Depends(get_db)):
    return await service.get_events(db, instrument_id, trader_id, from_time, to_time, limit, offset, replay_run_id)
