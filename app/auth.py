"""Minimal email + password auth for the MVP.

PBKDF2-hashed passwords, opaque bearer tokens in SQLite. Deliberately
simple; swap for a managed auth provider (Clerk/Auth0/Supabase) before
real launch without touching anything downstream — everything else only
ever sees a user_id.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid

from . import db

ITERATIONS = 200_000


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), ITERATIONS
    ).hex()


def register(email: str, password: str) -> str:
    """Create a user; returns an auth token. Raises ValueError on duplicates."""
    user_id = uuid.uuid4().hex
    salt = secrets.token_hex(16)
    with db.connect() as conn:
        try:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, salt, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, email.lower().strip(), _hash(password, salt), salt, time.time()),
            )
        except Exception as exc:
            raise ValueError("Email already registered") from exc
    return _issue_token(user_id)


def login(email: str, password: str) -> str:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, password_hash, salt FROM users WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()
    if row is None or not hmac.compare_digest(
        row["password_hash"], _hash(password, row["salt"])
    ):
        raise ValueError("Invalid email or password")
    return _issue_token(row["id"])


def user_id_for_token(token: str) -> str | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT user_id FROM auth_tokens WHERE token = ?", (token,)
        ).fetchone()
    return row["user_id"] if row else None


def _issue_token(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO auth_tokens (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, time.time()),
        )
    return token
