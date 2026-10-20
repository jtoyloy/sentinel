from datetime import datetime
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.models import Alert
from app.models.schemas import AlertResponse
from app.services.detection import DetectionEngine
import uuid
import hashlib
from sqlalchemy.exc import IntegrityError

class AlertingService:
    """Generates and manages compliance alerts from detections."""

    def __init__(self):
        self.detection_engine = DetectionEngine()

    async def create_alert_from_detection(
        self,
        db: AsyncSession,
        detection: dict
    ) -> AlertResponse:
        """Create an alert from a detection result."""
        detection_type = detection["type"]
        detection_data = detection["detection"]
        window_start = detection["window_start"]
        window_end = detection["window_end"]

        if detection_type == "WASH_TRADE":
            alert = await self._create_wash_trade_alert(
                db, detection_data, window_start, window_end
            )
        elif detection_type == "SPOOFING":
            alert = await self._create_spoofing_alert(
                db, detection_data, window_start, window_end
            )
        else:
            raise ValueError(f"Unknown detection type: {detection_type}")

        identity = alert.evidence["event_ids"]
        if detection_type == "SPOOFING":
            # More delayed opposite executions must not create a new case for
            # the same cancelled large order. Evidence is an observation snapshot.
            identity = [str(detection_data["spoof_order"].event_id),
                        str(detection_data["cancel_event"].event_id)]
        key = detection_type + ":v1:" + ",".join(sorted(identity))
        alert.fingerprint = hashlib.sha256(key.encode()).hexdigest()
        existing = await db.scalar(select(Alert).where(Alert.fingerprint == alert.fingerprint))
        if existing:
            return None
        try:
            async with db.begin_nested():
                db.add(alert)
                await db.flush()
        except IntegrityError:
            return None
        alert.explanation += (
            "\n\nObservation snapshot: this alert reflects evidence available when first detected. "
            "Late events may change the assessment; review the order history before disposition. "
            "This is a suspected pattern, not proof of intent. The score ranks heuristic evidence "
            "and is not a calibrated probability."
        )
        await db.flush()
        # Caller commits before publishing notifications.
        return AlertResponse.model_validate(alert)

    async def _create_wash_trade_alert(
        self,
        db: AsyncSession,
        detection: dict,
        window_start: datetime,
        window_end: datetime
    ) -> Alert:
        """Create alert for wash trade detection."""
        buy_event = detection["buy_event"]
        sell_event = detection["sell_event"]
        confidence = detection["confidence"]

        description = (
            f"Potential wash trade detected: accounts {buy_event.account_id} and {sell_event.account_id} executed "
            f"offsetting buy/sell of {buy_event.quantity} shares of {buy_event.instrument_id} "
            f"within {detection['time_diff_seconds']:.1f} seconds"
        )

        explanation = (
            f"Wash Trade Analysis:\n\n"
            f"Buy Order: {buy_event.order_id}\n"
            f"- Time: {buy_event.timestamp.isoformat()}\n"
            f"- Trader: {buy_event.trader_id}, Account: {buy_event.account_id}\n"
            f"- Quantity: {buy_event.quantity}, Price: ${buy_event.price}\n\n"
            f"Sell Order: {sell_event.order_id}\n"
            f"- Time: {sell_event.timestamp.isoformat()}\n"
            f"- Trader: {sell_event.trader_id}, Account: {sell_event.account_id}\n"
            f"- Quantity: {sell_event.quantity}, Price: ${sell_event.price}\n\n"
            f"Indicators:\n"
            f"- Same trader: {'Yes' if buy_event.trader_id == sell_event.trader_id else 'No'}\n"
            f"- Same firm: {'Yes' if buy_event.firm_id == sell_event.firm_id else 'No'}\n"
            f"- Ownership basis: {detection.get('ownership_basis', 'firm_and_account')}\n"
            f"- Buy beneficial owner: {(buy_event.event_metadata or {}).get('beneficial_owner', 'Not supplied')}\n"
            f"- Sell beneficial owner: {(sell_event.event_metadata or {}).get('beneficial_owner', 'Not supplied')}\n"
            f"- Time difference: {detection['time_diff_seconds']:.1f} seconds\n"
            f"- Price difference: ${detection['price_diff']:.2f}\n"
            f"- Quantity difference: {detection['qty_diff']} shares\n\n"
            f"This pattern suggests potential wash trading where the same beneficial owner "
            f"is trading with themselves to create artificial volume without risk."
        )

        evidence = {
            "event_ids": [str(buy_event.event_id), str(sell_event.event_id)],
            "ownership_basis": detection.get("ownership_basis", "firm_and_account"),
            "buy_event": {
                "event_id": str(buy_event.event_id),
                "order_id": buy_event.order_id,
                "timestamp": buy_event.timestamp.isoformat(),
                "trader_id": buy_event.trader_id,
                "account_id": buy_event.account_id,
                "quantity": buy_event.quantity,
                "price": float(buy_event.price)
            },
            "sell_event": {
                "event_id": str(sell_event.event_id),
                "order_id": sell_event.order_id,
                "timestamp": sell_event.timestamp.isoformat(),
                "trader_id": sell_event.trader_id,
                "account_id": sell_event.account_id,
                "quantity": sell_event.quantity,
                "price": float(sell_event.price)
            },
            "metrics": {
                "time_diff_seconds": detection["time_diff_seconds"],
                "price_diff": detection["price_diff"],
                "qty_diff": detection["qty_diff"]
            }
        }

        severity = "HIGH" if confidence > 0.8 else "MEDIUM" if confidence > 0.6 else "LOW"

        return Alert(
            alert_type="WASH_TRADE",
            severity=severity,
            status="OPEN",
            created_at=datetime.utcnow(),
            instrument_id=buy_event.instrument_id,
            time_window_start=window_start,
            time_window_end=window_end,
            description=description,
            explanation=explanation,
            evidence=evidence,
            confidence_score=confidence
        )

    async def _create_spoofing_alert(
        self,
        db: AsyncSession,
        detection: dict,
        window_start: datetime,
        window_end: datetime
    ) -> Alert:
        """Create alert for spoofing detection."""
        spoof_order = detection["spoof_order"]
        cancel_time = detection["cancel_time"]
        preceding_trades = detection["preceding_trades"]
        confidence = detection["confidence"]

        time_to_cancel = (cancel_time - spoof_order.timestamp).total_seconds()

        description = (
            f"Potential spoofing detected: {spoof_order.trader_id} placed large "
            f"{spoof_order.side} order of {spoof_order.quantity} shares of {spoof_order.instrument_id}, "
            f"cancelled after {time_to_cancel:.1f} seconds without execution"
        )

        explanation = (
            f"Spoofing Analysis:\n\n"
            f"Large Order (Spoof): {spoof_order.order_id}\n"
            f"- Placed: {spoof_order.timestamp.isoformat()}\n"
            f"- Cancelled: {cancel_time.isoformat()}\n"
            f"- Duration: {time_to_cancel:.1f} seconds\n"
            f"- Trader: {spoof_order.trader_id}, Account: {spoof_order.account_id}\n"
            f"- Side: {spoof_order.side}\n"
            f"- Quantity: {spoof_order.quantity} shares\n"
            f"- Price: ${spoof_order.price}\n\n"
            f"Opposite-Side Executions While Order Rested: {len(preceding_trades)}\n"
        )

        for i, trade in enumerate(preceding_trades, 1):
            explanation += (
                f"\nTrade {i}: {trade.order_id}\n"
                f"- Time: {trade.timestamp.isoformat()}\n"
                f"- Side: {trade.side}\n"
                f"- Quantity: {trade.quantity}, Price: ${trade.price}\n"
            )

        explanation += (
            f"\n\nIndicators:\n"
            f"- Large order size: {spoof_order.quantity} shares (threshold: 10,000)\n"
            f"- Quick cancellation: {time_to_cancel:.1f} seconds\n"
            f"- No execution of the large order observed in the evaluated window\n"
            f"- Accompanied by {len(preceding_trades)} opposite-side executions\n\n"
            f"This pattern suggests potential spoofing where a large order is placed to manipulate "
            f"perceived supply/demand, benefiting the trader's opposite-side positions, then cancelled "
            f"before execution."
        )

        event_ids = [str(spoof_order.event_id), str(detection["cancel_event"].event_id)]
        event_ids.extend([str(t.event_id) for t in preceding_trades])

        evidence = {
            "event_ids": event_ids,
            "spoof_order": {
                "event_id": str(spoof_order.event_id),
                "order_id": spoof_order.order_id,
                "timestamp": spoof_order.timestamp.isoformat(),
                "cancel_time": cancel_time.isoformat(),
                "trader_id": spoof_order.trader_id,
                "account_id": spoof_order.account_id,
                "side": spoof_order.side,
                "quantity": spoof_order.quantity,
                "price": float(spoof_order.price)
            },
            "preceding_trades": [
                {
                    "event_id": str(t.event_id),
                    "order_id": t.order_id,
                    "timestamp": t.timestamp.isoformat(),
                    "side": t.side,
                    "quantity": t.quantity,
                    "price": float(t.price)
                }
                for t in preceding_trades
            ],
            "metrics": {
                "time_to_cancel_seconds": time_to_cancel,
                "preceding_trade_count": len(preceding_trades)
            }
        }

        severity = "CRITICAL" if confidence > 0.8 else "HIGH" if confidence > 0.6 else "MEDIUM"

        return Alert(
            alert_type="SPOOFING",
            severity=severity,
            status="OPEN",
            created_at=datetime.utcnow(),
            instrument_id=spoof_order.instrument_id,
            time_window_start=window_start,
            time_window_end=window_end,
            description=description,
            explanation=explanation,
            evidence=evidence,
            confidence_score=confidence
        )

    async def get_alerts(
        self,
        db: AsyncSession,
        status: str = None,
        alert_type: str = None,
        instrument_id: str = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[AlertResponse]:
        """Query alerts with filters."""
        query = select(Alert)

        if status:
            query = query.where(Alert.status == status)
        if alert_type:
            query = query.where(Alert.alert_type == alert_type)
        if instrument_id:
            query = query.where(Alert.instrument_id == instrument_id)

        query = query.order_by(Alert.created_at.desc(), Alert.alert_id).offset(offset).limit(limit)

        result = await db.execute(query)
        alerts = result.scalars().all()

        return [AlertResponse.model_validate(a) for a in alerts]

    async def get_alert_by_id(
        self,
        db: AsyncSession,
        alert_id: uuid.UUID
    ) -> AlertResponse:
        """Get a specific alert by ID."""
        query = select(Alert).where(Alert.alert_id == alert_id)
        result = await db.execute(query)
        alert = result.scalar_one_or_none()

        if not alert:
            raise ValueError(f"Alert {alert_id} not found")

        return AlertResponse.model_validate(alert)
