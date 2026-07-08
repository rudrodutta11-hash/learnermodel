"""The conversation experience: talk with Kai, and the model learns from it.

Flow:
  POST /api/conversation/start     → engine picks concepts, Kai opens
  POST /api/conversation/{id}/message  → one learner turn, one Kai reply
  POST /api/conversation/{id}/end  → Kai assesses the transcript →
                                     learner-model update → fresh next rec

Voice-readiness (deliberate): this API is the transport-neutral core.
Every exchange is a plain (role, content) event — no channel assumptions.
Adding voice later means putting STT in front of `/message` and TTS behind
it (or a telephony bridge doing both); the session lifecycle, event log,
assessment, and learner-model updates all stay exactly as they are. The
full transcript already lands in `session_events`, so a voice call and a
text chat produce identical records downstream.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from learner_model import new_session_id
from recommendation import SessionContext

from . import db, state

router = APIRouter(prefix="/api/conversation")

OPENER_CUE = (
    "(The learner has just arrived for a {minutes:.0f}-minute conversation. "
    "Open it now: greet them briefly as their teacher and pull them into "
    "practicing the target concepts with an engaging first question.)"
)
WRAP_UP_GRACE = 1.5  # let a session run 50% over before Kai starts wrapping up


class StartRequest(BaseModel):
    minutes: float = Field(gt=0, le=60)


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


# -- helpers -------------------------------------------------------------------

def _session(session_id: str, user_id: str) -> dict:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversation_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return dict(row)


def _events(session_id: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT role, content, created_at FROM session_events "
            "WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _append_event(session_id: str, user_id: str, role: str, content: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO session_events (session_id, user_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, role, content, time.time()),
        )


def _teacher_context(session: dict, user_id: str) -> dict:
    rec = json.loads(session["recommendation"])
    profile = state.load_profile(user_id)
    return {
        "goal": rec["goal"],
        "concepts": rec["concepts"],
        "difficulty": rec["difficulty"],
        "minutes": session["minutes"],
        "learner": profile.summary(),
    }


def _as_chat_messages(events: list[dict]) -> list[dict]:
    """session_events → Messages-API shape (teacher speaks as assistant)."""
    return [
        {"role": "assistant" if e["role"] == "teacher" else "user",
         "content": e["content"]}
        for e in events
    ]


# -- routes --------------------------------------------------------------------

@router.post("/start")
def start(req: StartRequest, user_id: str = Depends(state.current_user)):
    profile = state.load_profile(user_id)
    ob = state.onboarding(user_id)
    ks = state.knowledge(user_id)

    rec = state.engine.recommend(profile, ks, SessionContext(
        subject=state.SUBJECT, available_minutes=req.minutes, goal=ob["goal"],
    ))
    # The engine chose WHAT (concepts, difficulty, why); this experience
    # delivers it conversationally regardless of which format it scored best.
    rec_snapshot = {
        "goal": ob["goal"],
        "concepts": list(rec.concepts),
        "difficulty": rec.difficulty,
        "need": rec.need,
        "explanation": rec.explanation,
    }

    session_id = new_session_id()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO conversation_sessions "
            "(id, user_id, started_at, minutes, recommendation) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, time.time(), req.minutes, json.dumps(rec_snapshot)),
        )

    context = {
        "goal": ob["goal"], "concepts": rec_snapshot["concepts"],
        "difficulty": rec.difficulty, "minutes": req.minutes,
        "learner": profile.summary(),
    }
    opener = state.teacher.chat(
        [{"role": "user", "content": OPENER_CUE.format(minutes=req.minutes)}],
        context,
    )
    _append_event(session_id, user_id, "teacher", opener)

    return {
        "session_id": session_id,
        "opener": opener,
        "concepts": rec_snapshot["concepts"],
        "explanation": rec.explanation,
        "minutes": req.minutes,
    }


@router.post("/{session_id}/message")
def message(session_id: str, req: MessageRequest,
            user_id: str = Depends(state.current_user)):
    session = _session(session_id, user_id)
    if session["ended_at"] is not None:
        raise HTTPException(status_code=409, detail="Conversation already ended")

    _append_event(session_id, user_id, "learner", req.content)

    context = _teacher_context(session, user_id)
    elapsed_min = (time.time() - session["started_at"]) / 60.0
    if elapsed_min > session["minutes"] * WRAP_UP_GRACE:
        context["time_note"] = (
            "The session is over its time budget — wrap up warmly within a "
            "message or two and suggest ending here."
        )

    reply = state.teacher.chat(_as_chat_messages(_events(session_id)), context)
    _append_event(session_id, user_id, "teacher", reply)

    return {
        "reply": reply,
        "elapsed_minutes": round(elapsed_min, 1),
        "budget_minutes": session["minutes"],
    }


@router.post("/{session_id}/end")
def end(session_id: str, user_id: str = Depends(state.current_user)):
    session = _session(session_id, user_id)
    if session["ended_at"] is not None:
        raise HTTPException(status_code=409, detail="Conversation already ended")

    now = time.time()
    with db.connect() as conn:
        conn.execute(
            "UPDATE conversation_sessions SET ended_at = ? WHERE id = ?",
            (now, session_id),
        )

    events = _events(session_id)
    context = _teacher_context(session, user_id)
    assessment = state.teacher.assess(events, context)

    learner_turns = sum(1 for e in events if e["role"] == "learner")
    summary = state.apply_session_results(
        user_id,
        activity_type="conversation",
        concepts=assessment["concepts"],
        mistakes=assessment["mistakes"],
        confidence=assessment["confidence"],
        duration_seconds=now - session["started_at"],
        difficulty=json.loads(session["recommendation"])["difficulty"],
        engaged=learner_turns > 0,  # a session with no learner turns = bounce
    )
    summary["teacher_notes"] = assessment.get("notes", "")

    return {
        "summary": summary,
        "next_recommendation": state.next_recommendation(user_id, session["minutes"]),
    }
