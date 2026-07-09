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
STORY_DIR = DATA_DIR / "story"


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
                recommendation TEXT NOT NULL,    -- JSON snapshot of the rec that opened it
                ai_calls INTEGER NOT NULL DEFAULT 0  -- realtime AI calls tallied live
            );
            -- Append-only interaction event log. INSERT only — this table
            -- has no UPDATE or DELETE path anywhere in the app. Every
            -- learner interaction (recommendation, activity lifecycle,
            -- chat turns, mistakes, summaries, profile updates) lands
            -- here as an immutable record so future model versions can
            -- replay the full history. See app/events.py.
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(id),
                session_id TEXT,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,           -- JSON, immutable once written
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, id);
            CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
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
            -- Kai's teaching journal: after each session he writes one
            -- private observation about HOW this student learns (pacing,
            -- confidence patterns, misconceptions, what analogy landed) —
            -- never personal-life details. Append-only, kept forever;
            -- recent entries are read back into his notes each session.
            CREATE TABLE IF NOT EXISTS kai_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(id),
                session_id TEXT,
                note TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_journal_user ON kai_journal(user_id, id);
            -- The Teacher Brain's predictions: made at session start,
            -- resolved at session end. The full prediction record (kind,
            -- confidence, evidence, and the outcome filled in later) lives
            -- in the JSON blob; the columns are just what we query on.
            -- Rows ARE updated in place when a prediction resolves — this is
            -- deliberately not append-only, because a prediction and its
            -- outcome are one evolving record, and calibration reads the
            -- resolved state.
            CREATE TABLE IF NOT EXISTS teacher_predictions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                session_id TEXT,
                kind TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                data TEXT NOT NULL              -- full Prediction JSON
            );
            CREATE INDEX IF NOT EXISTS idx_pred_user
                ON teacher_predictions(user_id, resolved, id);
            -- Cost ledger: one row per finished activity, recording how many
            -- AI calls it took and in which analysis mode — so spend can be
            -- attributed by activity_type and analysis_mode. `billable` is 0
            -- when the scripted/mock backend served it (no real credits).
            CREATE TABLE IF NOT EXISTS activity_costs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(id),
                session_id TEXT,
                activity_type TEXT NOT NULL,
                analysis_mode TEXT NOT NULL,
                ai_calls INTEGER NOT NULL,
                billable INTEGER NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_costs_user ON activity_costs(user_id, id);
        """)
