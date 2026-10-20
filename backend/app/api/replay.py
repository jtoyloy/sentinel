from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import require_analyst
from app.models.models import ReplayRun
from app.models.schemas import TradingEventCreate
from app.services.generator import SyntheticDataGenerator
from app.services.ingestion import IngestionService

router = APIRouter(prefix="/api/replay", tags=["replay"], dependencies=[Depends(require_analyst)])


@router.post("/{scenario}")
async def replay_scenario(scenario: Literal["wash_trade", "spoofing", "mixed", "normal"],
                          seed: int = Query(42, ge=0, le=2147483647),
                          base_time: AwareDatetime = datetime(2026, 10, 20, 14, 30, tzinfo=timezone.utc),
                          db: AsyncSession = Depends(get_db)):
    run_id = uuid4()
    prefix = run_id.hex[:12] + ":"
    generated = SyntheticDataGenerator(base_time=base_time, seed=seed).generate_scenario(scenario)
    for event in generated:
        for field in ("order_id", "parent_order_id", "trader_id", "account_id"):
            if event.get(field):
                event[field] = prefix + event[field]
        metadata = event.setdefault("metadata", {})
        for key in ("beneficial_owner", "counterparty"):
            if metadata.get(key):
                metadata[key] = prefix + metadata[key]
        metadata["replay_run_id"] = str(run_id)
    events = [TradingEventCreate(**event) for event in generated]
    db.add(ReplayRun(run_id=run_id, scenario=scenario, seed=seed,
                     base_time=base_time, event_count=len(events)))
    await IngestionService().ingest_batch(db, events, commit=False)
    await db.commit()
    return {"scenario": scenario, "seed": seed, "base_time": base_time,
            "run_id": run_id, "events_generated": len(events),
            "message": f"Persisted {len(events)} events; surveillance processing is queued."}
