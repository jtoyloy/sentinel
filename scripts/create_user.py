#!/usr/bin/env python3
"""Provision an account without a default password or command-line secret.

From repo root: backend/venv/bin/python scripts/create_user.py alice --role ADMIN
For automation supply SENTINEL_USER_PASSWORD; interactive use prompts twice.
"""
import argparse
import asyncio
import getpass
import os
from pathlib import Path
import re
import sys
backend_dir = Path(__file__).resolve().parents[1] / 'backend'
sys.path.insert(0, str(backend_dir if backend_dir.is_dir() else Path('/app')))
from sqlalchemy import select
from app.core.database import async_session_maker, engine
from app.core.security import hash_password
from app.models.auth import User


async def provision(username, role, password):
    async with async_session_maker() as db:
        if await db.scalar(select(User.id).where(User.username == username)):
            raise ValueError('Username already exists; existing account was not modified')
        db.add(User(username=username, role=role, password_hash=hash_password(password)))
        await db.commit()
    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('username')
    parser.add_argument('--role', choices=['ADMIN','ANALYST','VIEWER'], required=True)
    args = parser.parse_args()
    username = args.username.strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.@+-]{0,99}', username):
        parser.error('Username must contain 1..100 letters, digits, _, ., @, + or -')
    password = os.environ.get('SENTINEL_USER_PASSWORD')
    if password is None:
        password = getpass.getpass('Password (12+ characters): ')
        if password != getpass.getpass('Confirm password: '):
            parser.error('Passwords do not match')
    try:
        asyncio.run(provision(username, args.role, password))
    except ValueError as exc:
        parser.error(str(exc))
    print(f'Created {args.role} account: {username}')


if __name__ == '__main__':
    main()
