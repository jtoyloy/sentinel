from pydantic import BaseModel, Field, ConfigDict, AwareDatetime
from datetime import datetime
from decimal import Decimal
from typing import Optional, Literal, Annotated
from uuid import UUID

Identifier = Annotated[str, Field(min_length=1, max_length=50, pattern=r"\S")]
Status = Literal["OPEN", "INVESTIGATING", "RESOLVED", "FALSE_POSITIVE"]


class TradingEventCreate(BaseModel):
    event_id: Optional[UUID] = None
    event_type: Literal["ORDER", "EXECUTION", "CANCEL", "MODIFY"]
    timestamp: AwareDatetime
    instrument_id: Identifier
    trader_id: Identifier
    account_id: Identifier
    firm_id: Identifier
    side: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0, le=2147483647)
    price: Decimal = Field(gt=0, max_digits=20, decimal_places=8, allow_inf_nan=False)
    order_id: str = Field(min_length=1, max_length=100, pattern=r"\S")
    parent_order_id: Optional[str] = Field(None, min_length=1, max_length=100)
    metadata: dict = Field(default_factory=dict)


class TradingEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    event_id: UUID
    event_type: str
    timestamp: datetime
    instrument_id: str
    trader_id: str
    account_id: str
    firm_id: str
    side: str
    quantity: int
    price: float
    order_id: str
    parent_order_id: Optional[str]
    metadata: dict = Field(validation_alias="event_metadata", default_factory=dict)
    source_metadata: Optional[dict] = None
    created_at: datetime


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    alert_id: UUID
    alert_type: str
    severity: str
    status: str
    created_at: datetime
    instrument_id: str
    time_window_start: datetime
    time_window_end: datetime
    description: str
    explanation: str
    evidence: dict
    confidence_score: Optional[float]
    assigned_to: Optional[str]
    resolved_at: Optional[datetime]
    resolution_notes: Optional[str]


class AlertUpdate(BaseModel):
    status: Status
    actor: Optional[str] = Field(None, min_length=1, max_length=100, pattern=r"\S")
    notes: str = Field(min_length=1, max_length=4000, pattern=r"\S")


class AuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    alert_id: UUID
    previous_status: str
    new_status: str
    actor: str
    notes: str
    created_at: datetime
