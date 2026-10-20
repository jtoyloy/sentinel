from datetime import datetime, timedelta, timezone
import secrets
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, delete, text
from starlette.concurrency import run_in_threadpool
from app.core.database import get_db
from app.core.security import (COOKIE_NAME, cookie_secure, get_current_user, hash_password,
                               token_hash, validate_origin, verify_password)
from app.models.auth import User, AuthSession, LoginAttempt

router = APIRouter(prefix='/api/auth', tags=['authentication'])
# A real hash keeps unknown-user and invalid-password work comparable.
DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


@router.post('/login')
async def login(payload: LoginInput, request: Request, response: Response, db=Depends(get_db)):
    validate_origin(request)
    username = payload.username.strip().lower()
    identity = token_hash(username)
    remote = token_hash(request.client.host if request.client else 'unknown')
    now = datetime.now(timezone.utc)
    recent = now - timedelta(minutes=15)
    if db.bind.dialect.name == 'postgresql':
        # Serialize both identity and network buckets across concurrent API requests.
        for key in sorted({identity, remote}):
            lock_id = int(key[:16], 16) - (1 << 63)
            await db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_id})
    count = await db.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.created_at >= recent,
        or_(LoginAttempt.identity_hash == identity, LoginAttempt.remote_hash == remote)))
    if count >= 10:
        raise HTTPException(429, 'Too many login attempts; retry in 15 minutes', headers={'Retry-After': '900'})
    # Retain recent failures; prune expired throttling records to bound growth.
    await db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < recent))
    user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    valid = await run_in_threadpool(verify_password, payload.password, user.password_hash if user else DUMMY_HASH)
    if not valid or user is None or not user.active:
        db.add(LoginAttempt(identity_hash=identity, remote_hash=remote))
        await db.commit()
        raise HTTPException(401, 'Invalid username or password')
    # Rotate any existing browser session on successful authentication.
    old = request.cookies.get(COOKIE_NAME)
    if old:
        await db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(old)))
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(user_id=user.id, token_hash=token_hash(token), expires_at=now + timedelta(hours=8)))
    await db.commit()
    response.set_cookie(COOKIE_NAME, token, max_age=8 * 3600, httponly=True,
                        secure=cookie_secure(), samesite='lax', path='/')
    response.headers['Cache-Control'] = 'no-store'
    return {'username': user.username, 'role': user.role}


@router.get('/me')
async def me(response: Response, user=Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store'
    return {'username': user.username, 'role': user.role}


@router.post('/logout', status_code=204)
async def logout(request: Request, response: Response, db=Depends(get_db)):
    validate_origin(request)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        await db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    await db.commit()
    response.delete_cookie(COOKIE_NAME, httponly=True, secure=cookie_secure(), samesite='lax', path='/')
