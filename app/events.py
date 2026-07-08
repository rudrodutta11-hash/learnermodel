"""The append-only interaction event log.

Every learner interaction — not just the math the learner model consumes
today, but the full observable trail — is written here as an immutable,
timestamped record:

    recommendation_created    the engine decided what to do next
    activity_started          the learner began the recommended activity
    user_message               a learner turn in a conversation
    teacher_message            a teacher turn in a conversation
    mistake_detected           a specific mistake was identified
    activity_completed         the activity finished
    session_summarized         mastery/review-date were computed
    learner_profile_updated    the learner model changed as a result

This module has exactly one write primitive, `log()`, and it only ever
INSERTs. There is no update or delete path anywhere for this table —
history is never rewritten. `replay()` reads it back in order, which is
what lets a future, smarter model version reprocess everything a learner
has ever done and extract signal the current models don't yet use (e.g.
mining `user_message`/`teacher_message` content for signal beyond
correct/incorrect, or re-deriving `mistake_detected` patterns with a
better classifier) — the same replay guarantee `learner_model.history`
already provides for the core memory/modality/etc. math.
"""
from __future__ import annotations

import json
import time
from enum import Enum
from typing import Any

from . import db


class EventType(str, Enum):
    RECOMMENDATION_CREATED = "recommendation_created"
    ACTIVITY_STARTED = "activity_started"
    USER_MESSAGE = "user_message"
    TEACHER_MESSAGE = "teacher_message"
    MISTAKE_DETECTED = "mistake_detected"
    ACTIVITY_COMPLETED = "activity_completed"
    SESSION_SUMMARIZED = "session_summarized"
    LEARNER_PROFILE_UPDATED = "learner_profile_updated"


def log(
    user_id: str,
    event_type: EventType,
    payload: dict[str, Any],
    session_id: str | None = None,
) -> None:
    """Append one immutable event. Never call UPDATE or DELETE on this table."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO events (user_id, session_id, event_type, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, session_id, event_type.value,
             json.dumps(payload, default=str), time.time()),
        )


def replay(user_id: str, session_id: str | None = None) -> list[dict]:
    """The learner's full ordered event history (optionally scoped to one
    session) — what a future model version replays to learn from the past."""
    query = "SELECT * FROM events WHERE user_id = ?"
    params: list[Any] = [user_id]
    if session_id is not None:
        query += " AND session_id = ?"
        params.append(session_id)
    query += " ORDER BY id"
    with db.connect() as conn:
        rows = conn.execute(query, params).fetchall()
    events = []
    for row in rows:
        record = dict(row)
        record["payload"] = json.loads(record["payload"])
        events.append(record)
    return events


def messages(user_id: str, session_id: str) -> list[dict]:
    """Convenience view over one conversation's turns, oldest first —
    derived from the same immutable log, never a separate write path."""
    return [
        e for e in replay(user_id, session_id)
        if e["event_type"] in (EventType.USER_MESSAGE.value, EventType.TEACHER_MESSAGE.value)
    ]
