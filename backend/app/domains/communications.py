"""Synthetic communication capture and explainable, deterministic surveillance."""
import hashlib
import json
import random
import re
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Column, ForeignKey, JSON, String, Text, Uuid, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_reader, require_analyst
from app.domains.employees import Employee, EmployeeAccount, resolve_employee
from app.domains.data import Security
from app.models.models import Base, TradingEvent, UTCDateTime
from app.platform import ComplianceSignal, record_signal, serialize


class Communication(Base):
    __tablename__ = "communications"
    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_key = Column(String(200), nullable=False, unique=True)
    channel = Column(String(10), nullable=False)
    timestamp = Column(UTCDateTime(), nullable=False, index=True)
    sender = Column(String(254), nullable=False)
    recipients = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    subject = Column(String(500), nullable=False, default="")
    body = Column(Text, nullable=False)
    employee_id = Column(String(50), ForeignKey("employees.employee_id"), nullable=True, index=True)
    content_sha256 = Column(String(64), nullable=False)
    raw_payload = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)


def address(value):
    value = value.strip()
    if "@" not in value:  # chat employee identifiers are case-sensitive external IDs
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,50}", value):
            raise ValueError("invalid chat identity")
        return value
    _, email = parseaddr(value)
    email = email.casefold()
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email):
        raise ValueError("invalid email identity")
    # Parse only the mailbox; a trusted-looking display name never establishes identity.
    return email


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_key: str = Field(min_length=1, max_length=200, pattern=r"\S")
    channel: Literal["EMAIL", "CHAT"]
    timestamp: AwareDatetime
    sender: str = Field(min_length=1, max_length=254)
    recipients: list[str] = Field(min_length=1, max_length=100)
    subject: str = Field(default="", max_length=500)
    body: str = Field(min_length=1, max_length=100000, pattern=r"\S")

    @field_validator("sender")
    @classmethod
    def sender_valid(cls, value):
        address(value)
        return value

    @field_validator("recipients")
    @classmethod
    def recipients_valid(cls, value):
        for item in value:
            if not 1 <= len(item) <= 254:
                raise ValueError("recipient identifier length must be 1..254")
            address(item)
        return value


class IngestInput(BaseModel):
    messages: list[dict] = Field(min_length=1, max_length=1000)


RULES = {
    "GUARANTEED_RETURN": ("HIGH", r"\b(?:guaranteed\s+(?:returns?|profits?)|risk[ -]free\s+(?:returns?|profits?)|can(?:not|'t)\s+lose)\b", "Communication promises investment returns without risk."),
    "INSIDE_INFORMATION": ("HIGH", r"\b(?:inside\s+information|material\s+non[ -]public|before\s+(?:the\s+)?(?:announcement|earnings)|not\s+(?:yet\s+)?public)\b", "Communication refers to potential nonpublic information or trading before disclosure."),
    "CONCEALMENT": ("HIGH", r"\b(?:delete\s+(?:this|these|the)\s+(?:messages?|chats?|emails?)|off[ -]the[ -]record|hide\s+(?:this|the\s+trade)|avoid\s+(?:compliance|surveillance))\b", "Communication suggests concealing activity or evading surveillance."),
}
RULE_VERSION = "1"


def raw_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def normalize_message(raw):
    message = MessageInput.model_validate(raw)
    return {"source_key": message.source_key, "channel": message.channel, "timestamp": message.timestamp.astimezone(timezone.utc), "sender": address(message.sender), "recipients": [address(item) for item in message.recipients], "subject": message.subject.replace("\r\n", "\n"), "body": message.body.replace("\r\n", "\n")}


async def related_executions(db, communication, entities):
    if not communication.employee_id or not entities:
        return []
    accounts = (await db.scalars(select(EmployeeAccount).where(EmployeeAccount.employee_id == communication.employee_id))).all()
    rows = (await db.scalars(select(TradingEvent).where(TradingEvent.event_type == "EXECUTION", TradingEvent.timestamp >= communication.timestamp - timedelta(hours=24), TradingEvent.timestamp <= communication.timestamp + timedelta(hours=24), TradingEvent.instrument_id.in_([item["value"] for item in entities])))).all()
    keys = {(item.firm_id, item.account_id) for item in accounts}
    return [row for row in rows if (row.event_metadata or {}).get("employee_id") == communication.employee_id or (row.firm_id, row.account_id) in keys or row.trader_id == communication.employee_id]


def message_matches(communication, instruments):
    entities = []
    spans = {}
    for field in ("subject", "body"):
        text = getattr(communication, field)
        for match in re.finditer(r"\$([A-Za-z][A-Za-z0-9.]{0,9})\b", text):
            entities.append({"type": "INSTRUMENT", "value": match.group(1).upper(), "field": field, "start": match.start(), "end": match.end(), "text": match.group()})
        for instrument in sorted(instruments):
            for match in re.finditer(r"(?<![\w$])" + re.escape(instrument) + r"(?!\w)", text, re.IGNORECASE):
                entities.append({"type": "INSTRUMENT", "value": instrument.upper(), "field": field, "start": match.start(), "end": match.end(), "text": match.group()})
        for rule, (_, pattern, _) in RULES.items():
            for match in re.finditer(pattern, text, re.IGNORECASE):
                spans.setdefault(rule, []).append({"field": field, "start": match.start(), "end": match.end(), "text": match.group()})
    return entities, spans


async def detect_message(db, communication):
    # Instrument universe is drawn from actual retained events, plus explicit $tickers.
    instruments = set((await db.scalars(select(TradingEvent.instrument_id).distinct())).all())
    instruments.update((await db.scalars(select(Security.instrument_id))).all())
    entities, spans = message_matches(communication, instruments)
    events = await related_executions(db, communication, entities)
    generated = 0
    for rule, keywords in spans.items():
        severity, _, explanation = RULES[rule]
        explanation += f" Found {len(keywords)} phrase match(es) and {len(events)} employee-linked execution(s) within +/-24 hours. Sender identity is inferred from the synthetic feed mailbox, not cryptographically verified. This is a review signal, not a finding of misconduct."
        signal = await record_signal(db, domain="COMMUNICATION", rule=rule, fingerprint=f"{communication.id}:{RULE_VERSION}", severity="CRITICAL" if events and rule == "INSIDE_INFORMATION" else severity, title=rule.replace("_", " ").title(), explanation=explanation, evidence={"communications": [str(communication.id)], "events": [str(row.event_id) for row in events], "employees": [communication.employee_id] if communication.employee_id else [], "rule_version": RULE_VERSION, "keyword_spans": keywords, "entities": entities, "correlation_window_hours": 24, "content_sha256": communication.content_sha256, "sender_identity_assurance": "UNVERIFIED_FEED"})
        generated += signal is not None
    return generated


async def correlate_event(db: AsyncSession, event):
    """Append late-arrival evidence without changing any first-observation signal.

    Caller owns the transaction; stream replay uses the immutable event/message
    pair as a versioned idempotency key. Inactive profiles still warrant review
    for historical trading; correlation does not grant pre-trade permission.
    """
    if event.event_type != "EXECUTION":
        return []
    try:
        employee = await resolve_employee(db, account_id=event.account_id,
                                          firm_id=event.firm_id, trader_id=event.trader_id)
    except HTTPException as error:
        if error.status_code == 409:
            return []  # conflicting identity cannot establish employee linkage
        raise
    enriched_id = (event.event_metadata or {}).get("employee_id")
    if enriched_id is not None and (not isinstance(enriched_id, str) or not 1 <= len(enriched_id) <= 50):
        return []
    if employee and enriched_id and enriched_id != employee.employee_id:
        return []
    if employee is None and enriched_id:
        employee = await db.get(Employee, enriched_id)
    if employee is None:
        return []
    messages = (await db.scalars(select(Communication).where(
        Communication.timestamp >= event.timestamp - timedelta(hours=24),
        Communication.timestamp <= event.timestamp + timedelta(hours=24),
        or_(Communication.employee_id == employee.employee_id,
            Communication.sender.in_([employee.email, employee.employee_id]))
    ).order_by(Communication.timestamp, Communication.id))).all()
    generated = []
    for message in messages:
        # Address-based identity remains a synthetic feed assertion. Display
        # names never enter this check, and preserved identity cannot be reassigned.
        if message.employee_id and message.employee_id != employee.employee_id:
            continue
        if message.employee_id is None and message.sender not in (employee.email, employee.employee_id):
            continue
        if raw_hash(message.raw_payload) != message.content_sha256:
            continue
        normalized = normalize_message(message.raw_payload)
        if any(getattr(message, key) != value for key, value in normalized.items()):
            continue
        entities, spans = message_matches(message, {event.instrument_id})
        relevant_entities = [item for item in entities if item["value"] == event.instrument_id.upper()]
        if not spans or not relevant_entities:
            continue
        signal = await record_signal(
            db, domain="COMMUNICATION", rule="TRADE_CORRELATION",
            fingerprint=f"{message.id}:{event.event_id}:{RULE_VERSION}",
            severity="CRITICAL" if "INSIDE_INFORMATION" in spans else "HIGH",
            title="Suspicious communication linked to execution",
            explanation=(f"Preserved suspicious communication mentions {event.instrument_id} and is linked "
                         f"to execution {event.event_id} by employee {employee.employee_id} within +/-24 hours. "
                         f"Current employee status is {employee.status}; this historical review does not authorize trading. "
                         "Sender identity is inferred from the synthetic feed mailbox, not cryptographically verified. "
                         "This additive signal preserves the original communication assessment and does not establish misconduct."),
            evidence={"communications": [str(message.id)], "events": [str(event.event_id)],
                      "employees": [employee.employee_id], "rule_version": RULE_VERSION,
                      "matched_rules": sorted(spans),
                      "keyword_spans": [dict(item, rule=rule) for rule, matches in spans.items() for item in matches],
                      "entities": relevant_entities, "correlation_window_hours": 24,
                      "content_sha256": message.content_sha256,
                      "sender_identity_assurance": "UNVERIFIED_FEED", "employee_status": employee.status,
                      "identity_basis": "ENRICHED_EMPLOYEE" if enriched_id else "RESOLVED_ACCOUNT_OR_TRADER"})
        if signal is not None:
            generated.append(signal)
    return generated


async def ingest_communications(db: AsyncSession, messages):
    accepted = duplicates = generated = 0
    # Preflight complete batch: source-key conflicts cannot leave a partially committed feed.
    normalized = []
    seen = {}
    for raw in messages:
        payload = raw.model_dump(mode="json") if isinstance(raw, MessageInput) else raw
        try:
            values = normalize_message(payload)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        digest = raw_hash(payload)
        source_key = payload["source_key"]
        existing = await db.scalar(select(Communication).where(Communication.source_key == source_key))
        if existing and existing.content_sha256 != digest or source_key in seen and seen[source_key] != digest:
            raise HTTPException(409, "Communication source_key reused with different content")
        if existing or source_key in seen:
            duplicates += 1
        else:
            normalized.append((payload, digest, values))
        seen[source_key] = digest
    for payload, digest, values in normalized:
        sender = values["sender"]
        employee = await resolve_employee(db, email=sender if "@" in sender else None, trader_id=sender if "@" not in sender else None)
        message = Communication(**values, employee_id=employee.employee_id if employee else None, content_sha256=digest, raw_payload=payload)
        db.add(message)
        await db.flush()
        generated += await detect_message(db, message)
        accepted += 1
    return {"accepted": accepted, "duplicates": duplicates, "signals_generated": generated}


router = APIRouter(prefix="/api/communications", tags=["communications"], dependencies=[Depends(require_reader)])


@router.get("")
async def list_communications(channel: Optional[Literal["EMAIL", "CHAT"]] = None, employee_id: Optional[str] = Query(None, max_length=50), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db)):
    query = select(Communication)
    if channel:
        query = query.where(Communication.channel == channel)
    if employee_id:
        query = query.where(Communication.employee_id == employee_id)
    return [serialize(row) for row in (await db.scalars(query.order_by(Communication.timestamp.desc(), Communication.id).limit(limit).offset(offset))).all()]


@router.post("/ingest", dependencies=[Depends(require_analyst)])
async def ingest(body: IngestInput, db: AsyncSession = Depends(get_db)):
    result = await ingest_communications(db, body.messages)
    await db.commit()
    return result


def simulate_messages(seed=42):
    rng = random.Random(seed)
    base = datetime(2026, 10, 20, 14, 30, tzinfo=timezone.utc)
    bodies = ["Please review the $AAPL position and liquidity report before our routine meeting.", "We have inside information on $AAPL before the announcement. Buy today.", "This $MSFT strategy has guaranteed returns and risk-free profits.", "Delete this chat and avoid compliance. Move the $TSLA trade off-the-record."]
    return [{"source_key": f"synthetic-communications:{seed}:{i}:v1", "channel": "EMAIL" if i % 2 == 0 else "CHAT", "timestamp": (base + timedelta(minutes=i)).isoformat(), "sender": f"employee.{seed}.0@sentinel.example", "recipients": [f"employee.{seed}.1@sentinel.example"], "subject": rng.choice(["Market review", "Trading discussion"]), "body": body} for i, body in enumerate(bodies)]


@router.post("/simulate", dependencies=[Depends(require_analyst)])
async def simulate(seed: int = Query(42, ge=0, le=2147483647), db: AsyncSession = Depends(get_db)):
    result = await ingest_communications(db, simulate_messages(seed))
    await db.commit()
    return result


async def get_message(db, communication_id):
    row = await db.get(Communication, communication_id)
    if row is None:
        raise HTTPException(404, "Communication not found")
    return row


async def message_signals(db, message):
    rows = (await db.scalars(select(ComplianceSignal).where(ComplianceSignal.domain == "COMMUNICATION"))).all()
    return [row for row in rows if str(message.id) in row.evidence.get("communications", [])]


@router.get("/{communication_id}")
async def detail(communication_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    message = await get_message(db, communication_id)
    signals = await message_signals(db, message)
    event_ids = {uuid.UUID(value) for signal in signals for value in signal.evidence.get("events", [])}
    events = (await db.scalars(select(TradingEvent).where(TradingEvent.event_id.in_(event_ids)).order_by(TradingEvent.timestamp))).all() if event_ids else []
    from app.models.schemas import TradingEventResponse
    return {**serialize(message), "signals": [serialize(row) for row in signals], "related_events": [TradingEventResponse.model_validate(row).model_dump(mode="json") for row in events]}


@router.get("/{communication_id}/evidence")
async def evidence(communication_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    message = await get_message(db, communication_id)
    computed = raw_hash(message.raw_payload)
    try:
        normalized = normalize_message(message.raw_payload)
        normalized_valid = all(getattr(message, key) == value for key, value in normalized.items())
    except ValueError:
        normalized_valid = False
    return {**serialize(message), "hash_verified": computed == message.content_sha256 and normalized_valid, "computed_sha256": computed, "normalized_fields_verified": normalized_valid, "hash_algorithm": "SHA256", "hash_scope": "canonical UTF-8 raw_payload JSON"}
