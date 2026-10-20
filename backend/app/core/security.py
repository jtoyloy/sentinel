"""Server-side authentication and authorization; no client asserted actor identities."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import os
import secrets
from urllib.parse import urlsplit
from fastapi import Depends, HTTPException, Request, WebSocket
from sqlalchemy import select
from app.core.database import get_db
from app.core.config import settings
from app.models.auth import AuthSession, User

COOKIE_NAME = 'sentinel_session'
PASSWORD_ITERATIONS = 600_000


@dataclass(frozen=True)
class Principal:
    id: object
    username: str
    role: str


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 1024:
        raise ValueError('Password must contain 12 to 1024 characters')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS).hex()
    return f'pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or not 1 <= int(iterations) <= 2_000_000:
            return False
        digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def cookie_secure() -> bool:
    return os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true'


def validate_origin(request: Request):
    """Reject cross-site browser mutations; non-browser clients may omit Origin."""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Cross-site mutation rejected')
    origin = request.headers.get('origin')
    if origin is None:
        return
    allowed = {str(x).rstrip('/') for x in settings.CORS_ORIGINS.split(',') if x.strip()}
    # Same-origin requests work behind the Vite/nginx reverse proxy as well.
    allowed.add(str(request.base_url).rstrip('/'))
    parsed = urlsplit(origin)
    if parsed.scheme not in ('http', 'https') or origin.rstrip('/') not in allowed:
        raise HTTPException(403, 'Untrusted request origin')


async def resolve_session(cookies, db) -> Principal:
    token = cookies.get(COOKIE_NAME)
    if not token or len(token) > 256:
        raise HTTPException(401, 'Authentication required')
    result = await db.execute(select(User).join(AuthSession, AuthSession.user_id == User.id).where(
        AuthSession.token_hash == token_hash(token), AuthSession.expires_at > datetime.now(timezone.utc), User.active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(401, 'Session expired or invalid')
    return Principal(user.id, user.username, user.role)


async def get_current_user(request: Request, db=Depends(get_db)) -> Principal:
    validate_origin(request)
    return await resolve_session(request.cookies, db)


async def websocket_principal(websocket: WebSocket, db) -> Principal:
    origin = websocket.headers.get('origin')
    if origin:
        allowed = {x.strip().rstrip('/') for x in settings.CORS_ORIGINS.split(',') if x.strip()}
        scheme = 'https' if websocket.url.scheme == 'wss' else 'http'
        allowed.add(f'{scheme}://{websocket.url.netloc}')
        if origin.rstrip('/') not in allowed:
            raise HTTPException(403, 'Untrusted WebSocket origin')
    if websocket.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Cross-site WebSocket rejected')
    return await resolve_session(websocket.cookies, db)


async def require_reader(user: Principal = Depends(get_current_user)) -> Principal:
    if user.role not in ('VIEWER', 'ANALYST', 'ADMIN'):
        raise HTTPException(403, 'Reader role required')
    return user


async def require_analyst(user: Principal = Depends(require_reader)) -> Principal:
    if user.role not in ('ANALYST', 'ADMIN'):
        raise HTTPException(403, 'Analyst role required')
    return user


async def require_admin(user: Principal = Depends(require_reader)) -> Principal:
    if user.role != 'ADMIN':
        raise HTTPException(403, 'Administrator role required')
    return user
