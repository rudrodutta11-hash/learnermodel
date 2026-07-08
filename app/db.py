"""SQLite persistence for the MVP app layer.

Stores users, auth tokens, and onboarding data. Learner profiles are kept
as JSON documents (via learner_model.store) and session history as JSONL
(via learner_model.history) under DATA_DIR — the learner model owns its
own persistence formats so its storage can evolve independently of the
web app.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.environ.get("LEARNER_DATA_DIR", "data"))
PROFILES_DIR = DATA_DIR / "profiles"
HISTORY_DIR = DATA_DIR / "history"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA_DIR / "app.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_tokens (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS onboarding (
                user_id TEXT PRIMARY KEY REFERENCES users(id),
                goal TEXT NOT NULL,
                level TEXT NOT NULL,
                interests TEXT NOT NULL,        -- JSON array
                weekly_minutes INTEGER NOT NULL,
                motivation TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversation_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                started_at REAL NOT NULL,
                ended_at REAL,
                minutes REAL NOT NULL,
                recommendation TEXT NOT NULL     -- JSON snapshot of the rec that opened it
            );
            CREATE TABLE IF NOT EXISTS session_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES conversation_sessions(id),
                user_id TEXT NOT NULL REFERENCES users(id),
                role TEXT NOT NULL,              -- 'learner' | 'teacher'
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS session_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(id),
                created_at REAL NOT NULL,
                activity_type TEXT NOT NULL,
                concepts TEXT NOT NULL,          -- JSON array
                mistakes TEXT NOT NULL,          -- JSON array of error signatures
                confidence REAL,
                estimated_mastery REAL,
                review_due_at REAL
            );
        """)
