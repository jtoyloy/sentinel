from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from uuid import UUID
from app.core.database import get_db
from app.models.models import Alert, TradingEvent, AlertAudit
from app.models.schemas import AlertResponse, TradingEventResponse, AlertUpdate, AuditResponse
from app.services.alerting import AlertingService

router = APIRouter(prefix="/api/alerts", tags=["alerts"])
service = AlertingService()


@router.get("", response_model=List[AlertResponse])
async def get_alerts(status: Optional[str] = None, alert_type: Optional[str] = None,
                     instrument_id: Optional[str] = None, limit: int = Query(100, ge=1, le=1000),
                     offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db)):
    return await service.get_alerts(db, status, alert_type, instrument_id, limit, offset)


async def find_alert(db, alert_id, lock=False):
    query = select(Alert).where(Alert.alert_id == alert_id)
    if lock:
        query = query.with_for_update()
    alert = await db.scalar(query)
    if alert is None:
        raise HTTPException(404, "Alert not found")
    return alert


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(alert_id: UUID, db: AsyncSession = Depends(get_db)):
    return await find_alert(db, alert_id)


@router.get("/{alert_id}/evidence", response_model=List[TradingEventResponse])
async def evidence(alert_id: UUID, db: AsyncSession = Depends(get_db)):
    alert = await find_alert(db, alert_id)
    ids = [UUID(value) for value in alert.evidence["event_ids"]]
    return (await db.scalars(select(TradingEvent).where(TradingEvent.event_id.in_(ids)).order_by(
        TradingEvent.timestamp, TradingEvent.event_id))).all()


@router.patch("/{alert_id}", response_model=AlertResponse)
async def update_alert(alert_id: UUID, update: AlertUpdate, db: AsyncSession = Depends(get_db)):
    alert = await find_alert(db, alert_id, lock=True)
    db.add(AlertAudit(alert_id=alert_id, previous_status=alert.status,
                      new_status=update.status, actor=update.actor, notes=update.notes))
    alert.status = update.status
    alert.assigned_to = update.actor
    alert.resolution_notes = update.notes
    alert.resolved_at = datetime.now(timezone.utc) if update.status in ("RESOLVED", "FALSE_POSITIVE") else None
    await db.commit()
    return alert


@router.get("/{alert_id}/history", response_model=List[AuditResponse])
async def history(alert_id: UUID, db: AsyncSession = Depends(get_db)):
    await find_alert(db, alert_id)
    return (await db.scalars(select(AlertAudit).where(AlertAudit.alert_id == alert_id).order_by(
        AlertAudit.created_at, AlertAudit.id))).all()
