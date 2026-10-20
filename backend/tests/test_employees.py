"""Employee domain tests use committed SQLite state and real HTTP routes."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_db
from app.core.security import get_current_user
from app.domains.employees import (Employee, EmployeeAccount, EmployeeChange, FeedIssue,
    enrich_event, ingest_hr_feed, resolve_employee, router, simulate_hr_records)
from app.models.models import Base


@pytest_asyncio.fixture
async def hr(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/hr.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)
    async def db_dependency():
        async with maker() as db:
            yield db
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="test", username="analyst", role="ANALYST")
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        yield maker, client, app
    await engine.dispose()


@pytest.mark.asyncio
async def test_feed_persistence_normalization_idempotency_and_detail(hr):
    maker, client, _ = hr
    records = simulate_hr_records(5, 3)
    records[0]["email"] = "  Employee.5.0@Sentinel.Example  "
    response = await client.post("/api/employees/feed", json={"records": records})
    assert response.status_code == 200, response.text
    assert response.json() == {"accepted": 3, "rejected": 0, "duplicates": 0, "issues": []}
    result = await client.post("/api/employees/feed", json={"records": records})
    assert result.json()["duplicates"] == 3
    async with maker() as db:
        employee = await db.get(Employee, "EMP-5-0000")
        assert employee.email == "employee.5.0@sentinel.example"
        assert await db.scalar(select(func.count()).select_from(EmployeeChange)) == 3
        assert await db.scalar(select(func.count()).select_from(EmployeeAccount)) == 3
    rows = (await client.get("/api/employees", params={"search": "employee.5.0"})).json()
    assert len(rows) == 1 and rows[0]["account_count"] == 1
    detail = (await client.get("/api/employees/EMP-5-0000")).json()
    assert detail["history"][0]["payload"]["source_key"] == records[0]["source_key"]
    assert detail["accounts"][0]["account_id"] == "HR-5-0000"


@pytest.mark.asyncio
async def test_temporal_changes_and_mapping_replacement_do_not_revert(hr):
    maker, client, _ = hr
    initial = simulate_hr_records(3, 1)[0]
    await client.post("/api/employees/feed", json={"records": [initial]})
    newer = {**initial, "source_key": "promotion", "department": "Compliance", "role": "Director", "effective_at": "2026-03-01T00:00:00Z", "accounts": [{"firm_id": "NEW-FIRM", "account_id": "NEW-ACCOUNT"}]}
    stale = {**initial, "source_key": "late-january", "department": "Research", "effective_at": "2026-01-02T00:00:00Z"}
    result = await client.post("/api/employees/feed", json={"records": [newer, stale]})
    assert result.json()["accepted"] == 2
    async with maker() as db:
        employee = await db.get(Employee, initial["employee_id"])
        assert employee.department == "Compliance" and employee.role == "Director"
        assert await resolve_employee(db, account_id="HR-3-0000", firm_id="SYNTHETIC") is None
        assert (await resolve_employee(db, account_id="NEW-ACCOUNT", firm_id="NEW-FIRM")).employee_id == employee.employee_id
    history = (await client.get(f"/api/employees/{initial['employee_id']}")).json()["history"]
    assert [item["source_key"] for item in history] == [initial["source_key"], "late-january", "promotion"]


@pytest.mark.asyncio
async def test_corrupt_records_and_conflicts_persist_issues_without_losing_valid(hr):
    maker, client, _ = hr
    records = simulate_hr_records(2, 4)
    records[1]["email"] = "invalid"
    records[2]["effective_at"] = "2026-01-01T00:00:00"  # timezone required
    records[3]["accounts"] = records[0]["accounts"]  # cannot steal an account
    result = (await client.post("/api/employees/feed", json={"records": records})).json()
    assert result["accepted"] == 1 and result["rejected"] == 3
    changed = {**records[0], "department": "Different"}
    result = (await client.post("/api/employees/feed", json={"records": [changed]})).json()
    assert result["rejected"] == 1 and "reused" in result["issues"][0]["reason"]
    async with maker() as db:
        assert await db.scalar(select(func.count()).select_from(FeedIssue)) == 4
        assert await db.scalar(select(func.count()).select_from(Employee)) == 1
        assert (await db.get(Employee, records[0]["employee_id"])).department == records[0]["department"]
    assert len((await client.get("/api/employees/quality")).json()) == 4


@pytest.mark.asyncio
async def test_resolution_ambiguity_enrichment_and_false_matches(hr):
    maker, client, _ = hr
    records = simulate_hr_records(10, 2)
    records[0]["accounts"] = [{"firm_id": "FIRM-A", "account_id": "SHARED"}]
    records[1]["accounts"] = [{"firm_id": "FIRM-B", "account_id": "SHARED"}]
    await client.post("/api/employees/feed", json={"records": records})
    async with maker() as db:
        assert (await resolve_employee(db, email=records[0]["email"].upper())).employee_id == records[0]["employee_id"]
        assert await resolve_employee(db, account_id="SHARED", firm_id="UNKNOWN") is None
        with pytest.raises(HTTPException) as error:
            await resolve_employee(db, account_id="SHARED")
        assert error.value.status_code == 409
        with pytest.raises(HTTPException):
            await resolve_employee(db, trader_id=records[0]["employee_id"], email=records[1]["email"])
        assert await enrich_event(db, {"account_id": "unknown", "firm_id": "FIRM-A"}) == {}
        enriched = await enrich_event(db, {"account_id": "SHARED", "firm_id": "FIRM-A"})
        assert enriched["employee_id"] == records[0]["employee_id"] and enriched["department"] == records[0]["department"]
    assert (await client.get("/api/employees/resolve", params={"identity": "SHARED"})).status_code == 409
    assert (await client.get("/api/employees/resolve", params={"identity": "unknown"})).status_code == 404
    assert (await client.get("/api/employees/resolve", params={"identity": records[0]["email"]})).json()["match_type"] == "EMAIL"


@pytest.mark.asyncio
async def test_simulator_determinism_pagination_and_authorization(hr):
    _, client, app = hr
    assert simulate_hr_records(42, 12) == simulate_hr_records(42, 12)
    assert simulate_hr_records(42, 12) != simulate_hr_records(43, 12)
    assert (await client.post("/api/employees/simulate?count=4")).json()["accepted"] == 4
    assert (await client.post("/api/employees/simulate?count=4")).json()["duplicates"] == 4
    assert len((await client.get("/api/employees?limit=2&offset=2")).json()) == 2
    assert (await client.get("/api/employees?limit=1001")).status_code == 422
    assert (await client.get("/api/employees?offset=-1")).status_code == 422
    assert (await client.post("/api/employees/feed", json={"records": []})).status_code == 422
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="viewer", username="viewer", role="VIEWER")
    assert (await client.get("/api/employees")).status_code == 200
    assert (await client.post("/api/employees/simulate")).status_code == 403


@pytest.mark.asyncio
async def test_service_is_flush_only_and_rollback_is_atomic(hr):
    maker, _, _ = hr
    async with maker() as db:
        # SQLite requires an outer write transaction before savepoints; the first
        # issue establishes it, matching PostgreSQL's transactional savepoints.
        db.add(FeedIssue(source="TEST", source_key="outer", reason="transaction test", payload={}))
        await db.flush()
        result = await ingest_hr_feed(db, simulate_hr_records(77, 1))
        assert result["accepted"] == 1
        await db.rollback()
    async with maker() as db:
        assert await db.get(Employee, "EMP-77-0000") is None
        assert await db.scalar(select(func.count()).select_from(EmployeeChange)) == 0
