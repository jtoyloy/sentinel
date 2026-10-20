"""Persisted identities, revocable sessions and login throttling records."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, ForeignKey, CheckConstraint
from app.models.models import Base, UTCDateTime
from sqlalchemy import Uuid


def now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = 'users'
    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(100), nullable=False, unique=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(20), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(UTCDateTime(), nullable=False, default=now)
    __table_args__ = (CheckConstraint("role IN ('VIEWER','ANALYST','ADMIN')", name='check_user_role'),)


class AuthSession(Base):
    __tablename__ = 'auth_sessions'
    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid(as_uuid=True), ForeignKey('users.id'), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    created_at = Column(UTCDateTime(), nullable=False, default=now)
    expires_at = Column(UTCDateTime(), nullable=False, index=True)


class LoginAttempt(Base):
    __tablename__ = 'login_attempts'
    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    identity_hash = Column(String(64), nullable=False, index=True)
    remote_hash = Column(String(64), nullable=False, index=True)
    created_at = Column(UTCDateTime(), nullable=False, default=now, index=True)
