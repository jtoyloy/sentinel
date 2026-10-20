"""Real persisted authentication, expiry, CSRF and role boundaries."""
from datetime import datetime, timedelta, timezone
import pytest
import pytest_asyncio
import httpx
from fastapi import FastAPI, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.api.auth import router
from app.core.database import get_db
from app.core.security import hash_password, verify_password, require_reader, require_analyst, require_admin, token_hash
from app.models.auth import User, AuthSession, LoginAttempt
from app.models.models import Base

PASSWORD = 'Test-only-password-217!'


@pytest_asyncio.fixture
async def authenticated_app():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        encoded = hash_password(PASSWORD)
        for role in ('VIEWER', 'ANALYST', 'ADMIN'):
            db.add(User(username=role.lower(), password_hash=encoded, role=role))
        await db.commit()
    app = FastAPI()
    app.include_router(router)
    async def database():
        async with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    @app.get('/reader')
    async def read(user=Depends(require_reader)):
        return {'actor': user.username}
    @app.post('/analyst')
    async def mutate(user=Depends(require_analyst)):
        return {'actor': user.username}
    @app.post('/admin')
    async def admin(user=Depends(require_admin)):
        return {'actor': user.username}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield client, sessions
    await engine.dispose()


def test_password_salted_and_invalid_hash():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second
    assert PASSWORD not in first
    assert verify_password(PASSWORD, first)
    assert not verify_password('incorrect', first)
    assert not verify_password(PASSWORD, 'invalid-hash')
    with pytest.raises(ValueError):
        hash_password('short')


@pytest.mark.asyncio
async def test_sessions_are_hashed_revocable_and_expire(authenticated_app):
    client, sessions = authenticated_app
    assert (await client.get('/api/auth/me')).status_code == 401
    response = await client.post('/api/auth/login', json={'username':'ADMIN','password':PASSWORD})
    assert response.status_code == 200
    assert response.json() == {'username':'admin','role':'ADMIN'}
    cookie = response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=lax' in cookie
    token = client.cookies['sentinel_session']
    async with sessions() as db:
        session = (await db.execute(select(AuthSession))).scalar_one()
        assert session.token_hash == token_hash(token) and session.token_hash != token
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
    assert (await client.get('/api/auth/me')).status_code == 401
    assert (await client.post('/api/auth/login', json={'username':'admin','password':PASSWORD})).status_code == 200
    assert client.cookies['sentinel_session'] != token
    assert (await client.post('/api/auth/logout')).status_code == 204
    assert (await client.get('/api/auth/me')).status_code == 401
    async with sessions() as db:
        assert (await db.execute(select(AuthSession))).scalars().all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize('role,analyst,admin', [('viewer',403,403),('analyst',200,403),('admin',200,200)])
async def test_server_role_enforcement(authenticated_app, role, analyst, admin):
    client, _ = authenticated_app
    assert (await client.post('/admin')).status_code == 401
    await client.post('/api/auth/login', json={'username':role,'password':PASSWORD})
    assert (await client.get('/reader')).json() == {'actor':role}
    assert (await client.post('/analyst', json={'role':'ADMIN'})).status_code == analyst
    assert (await client.post('/admin', json={'role':'ADMIN'})).status_code == admin


@pytest.mark.asyncio
async def test_origin_and_disabled_account(authenticated_app):
    client, sessions = authenticated_app
    payload = {'username':'admin','password':PASSWORD}
    assert (await client.post('/api/auth/login', json=payload, headers={'Origin':'https://evil.invalid'})).status_code == 403
    assert (await client.post('/api/auth/login', json=payload, headers={'Origin':'null'})).status_code == 403
    assert (await client.post('/api/auth/login', json=payload, headers={'Origin':'http://test'})).status_code == 200
    assert (await client.post('/admin', headers={'Origin':'https://evil.invalid'})).status_code == 403
    assert (await client.post('/admin', headers={'sec-fetch-site':'cross-site'})).status_code == 403
    async with sessions() as db:
        user = (await db.execute(select(User).where(User.username=='admin'))).scalar_one()
        user.active = False
        await db.commit()
    assert (await client.get('/reader')).status_code == 401
    assert (await client.post('/api/auth/login', json=payload)).status_code == 401


@pytest.mark.asyncio
async def test_persistent_login_throttle(authenticated_app):
    client, sessions = authenticated_app
    for _ in range(10):
        assert (await client.post('/api/auth/login', json={'username':'admin','password':'wrong'})).status_code == 401
    response = await client.post('/api/auth/login', json={'username':'admin','password':PASSWORD})
    assert response.status_code == 429
    assert response.headers['retry-after'] == '900'
    # A fresh browser/session cannot reset database-backed rate limits.
    client.cookies.clear()
    assert (await client.post('/api/auth/login', json={'username':'other','password':'wrong'})).status_code == 429
    async with sessions() as db:
        attempts = (await db.execute(select(LoginAttempt))).scalars().all()
        assert len(attempts) == 10
        for attempt in attempts:
            attempt.created_at = datetime.now(timezone.utc) - timedelta(minutes=16)
        await db.commit()
    assert (await client.post('/api/auth/login', json={'username':'admin','password':PASSWORD})).status_code == 200
