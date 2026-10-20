"""Event-time surveillance heuristics. Scores rank evidence; they are not probabilities."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import TradingEvent


class DetectionEngine:
    def __init__(self):
        self.wash_trade_window = timedelta(seconds=30)
        self.wash_trade_price_tolerance = Decimal("0.01")
        self.spoofing_cancel_window = timedelta(seconds=15)
        self.spoofing_size_threshold = 10000

    @staticmethod
    def _same_account(first, second):
        return first.firm_id == second.firm_id and first.account_id == second.account_id

    @classmethod
    def _common_owner(cls, first, second):
        first_owner = (first.event_metadata or {}).get("beneficial_owner")
        second_owner = (second.event_metadata or {}).get("beneficial_owner")
        if first_owner or second_owner:
            return bool(first_owner and second_owner and first_owner == second_owner)
        return cls._same_account(first, second)

    async def _events(self, db, instrument_id, start, end, event_type=None):
        query = select(TradingEvent).where(
            TradingEvent.instrument_id == instrument_id,
            TradingEvent.timestamp >= start,
            TradingEvent.timestamp <= end,
        )
        if event_type:
            query = query.where(TradingEvent.event_type == event_type)
        result = await db.execute(query.order_by(TradingEvent.timestamp, TradingEvent.event_id))
        return result.scalars().all()

    async def detect_wash_trades(
        self, db: AsyncSession, instrument_id: str,
        time_window_start: datetime, time_window_end: datetime,
    ) -> List[Dict]:
        executions = await self._events(db, instrument_id, time_window_start, time_window_end, "EXECUTION")
        detections = []
        # Pair chronological neighbors of either side: a sell can precede a buy.
        for index, first in enumerate(executions):
            for second in executions[index + 1:]:
                time_diff = (second.timestamp - first.timestamp).total_seconds()
                if time_diff > self.wash_trade_window.total_seconds():
                    break
                if first.side == second.side or not self._common_owner(first, second):
                    continue
                buy, sell = (first, second) if first.side == "BUY" else (second, first)
                price_diff = abs(Decimal(buy.price) - Decimal(sell.price))
                # Symmetric tolerance avoids a different result when temporal order changes.
                if price_diff > min(Decimal(buy.price), Decimal(sell.price)) * self.wash_trade_price_tolerance:
                    continue
                qty_diff = abs(buy.quantity - sell.quantity)
                if Decimal(qty_diff) > Decimal(max(buy.quantity, sell.quantity)) * Decimal("0.1"):
                    continue
                detections.append({
                    "buy_event": buy, "sell_event": sell,
                    "time_diff_seconds": time_diff, "price_diff": float(price_diff),
                    "qty_diff": qty_diff,
                    "ownership_basis": "beneficial_owner" if (buy.event_metadata or {}).get("beneficial_owner") else "firm_and_account",
                    "confidence": self._calculate_wash_trade_confidence(buy, sell, time_diff, float(price_diff), qty_diff),
                })
        return detections

    def _calculate_wash_trade_confidence(self, buy_exec, sell_exec, time_diff, price_diff, qty_diff):
        score = 0.55
        if (buy_exec.event_metadata or {}).get("beneficial_owner"):
            score += 0.15
        if time_diff < 5:
            score += 0.1
        elif time_diff < 10:
            score += 0.05
        if price_diff < 0.01:
            score += 0.1
        if qty_diff == 0:
            score += 0.1
        return round(min(score, 1.0), 4)

    async def detect_spoofing(
        self, db: AsyncSession, instrument_id: str,
        time_window_start: datetime, time_window_end: datetime,
    ) -> List[Dict]:
        events = await self._events(db, instrument_id, time_window_start, time_window_end)
        detections = []
        for order in events:
            if order.event_type != "ORDER" or order.quantity < self.spoofing_size_threshold:
                continue
            related = [event for event in events if
                       self._same_account(order, event) and event.side == order.side and
                       (event.order_id == order.order_id or event.parent_order_id == order.order_id)]
            cancellations = [event for event in related if
                             event.event_type == "CANCEL" and
                             timedelta(0) <= event.timestamp - order.timestamp <= self.spoofing_cancel_window]
            if not cancellations:
                continue
            cancel = cancellations[0]
            if any(event.event_type == "EXECUTION" and event.timestamp >= order.timestamp for event in related):
                continue
            opposite = "SELL" if order.side == "BUY" else "BUY"
            trades = [event for event in events if
                      event.event_type == "EXECUTION" and event.side == opposite and
                      self._same_account(order, event) and
                      order.timestamp <= event.timestamp <= cancel.timestamp]
            if not trades:
                continue
            detections.append({
                "spoof_order": order, "cancel_event": cancel, "cancel_time": cancel.timestamp,
                # Retained as a compatibility key; these executions occur while the order rests.
                "preceding_trades": trades, "opposite_trades": trades,
                "confidence": self._calculate_spoofing_confidence(order, cancel.timestamp, trades),
            })
        return detections

    def _calculate_spoofing_confidence(self, spoof_order, cancel_time, preceding_trades):
        score = 0.4
        seconds = (cancel_time - spoof_order.timestamp).total_seconds()
        score += 0.2 if seconds < 5 else 0.1 if seconds < 10 else 0
        score += 0.2 if len(preceding_trades) >= 3 else 0.15 if len(preceding_trades) >= 2 else 0.1
        if spoof_order.quantity > 50000:
            score += 0.1
        return round(min(score, 1.0), 4)

    async def process_detection_window(
        self, db: AsyncSession, instrument_id: str, window_minutes: int = 5,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        end_time = end_time or datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=window_minutes)
        detections = []
        for kind, detector in (("WASH_TRADE", self.detect_wash_trades), ("SPOOFING", self.detect_spoofing)):
            for result in await detector(db, instrument_id, start_time, end_time):
                detections.append({"type": kind, "detection": result,
                                   "window_start": start_time, "window_end": end_time})
        return detections
