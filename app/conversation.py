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
full transcript already lands in the append-only `events` log (as
`user_message`/`teacher_message` events), so a voice call and a text chat
produce identical records downstream.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from learner_model import new_session_id
from recommendation import SessionContext

from . import db, state
from . import events as events_log
from .events import EventType

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


def _transcript(session_id: str, user_id: str) -> list[dict]:
    """The conversation so far as {"role": "learner"|"teacher", "content"}
    turns, read straight from the append-only event log."""
    role_by_event_type = {
        EventType.USER_MESSAGE.value: "learner",
        EventType.TEACHER_MESSAGE.value: "teacher",
    }
    return [
        {"role": role_by_event_type[e["event_type"]], "content": e["payload"]["content"]}
        for e in events_log.messages(user_id, session_id)
    ]


def _log_turn(session_id: str, user_id: str, role: str, content: str) -> None:
    event_type = EventType.TEACHER_MESSAGE if role == "teacher" else EventType.USER_MESSAGE
    events_log.log(user_id, event_type, {"content": content}, session_id=session_id)


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


def _as_chat_messages(transcript: list[dict]) -> list[dict]:
    """Transcript turns → Messages-API shape (teacher speaks as assistant)."""
    return [
        {"role": "assistant" if t["role"] == "teacher" else "user",
         "content": t["content"]}
        for t in transcript
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

    events_log.log(user_id, EventType.RECOMMENDATION_CREATED, {
        "activity_type": "conversation", "difficulty": rec.difficulty,
        "concepts": rec_snapshot["concepts"], "need": rec.need,
        "explanation": rec.explanation, "estimated_minutes": req.minutes,
    }, session_id=session_id)
    events_log.log(user_id, EventType.ACTIVITY_STARTED, {
        "activity_type": "conversation", "minutes": req.minutes,
    }, session_id=session_id)

    context = {
        "goal": ob["goal"], "concepts": rec_snapshot["concepts"],
        "difficulty": rec.difficulty, "minutes": req.minutes,
        "learner": profile.summary(),
    }
    opener = state.teacher.chat(
        [{"role": "user", "content": OPENER_CUE.format(minutes=req.minutes)}],
        context,
    )
    _log_turn(session_id, user_id, "teacher", opener)

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

    _log_turn(session_id, user_id, "learner", req.content)

    context = _teacher_context(session, user_id)
    elapsed_min = (time.time() - session["started_at"]) / 60.0
    if elapsed_min > session["minutes"] * WRAP_UP_GRACE:
        context["time_note"] = (
            "The session is over its time budget — wrap up warmly within a "
            "message or two and suggest ending here."
        )

    reply = state.teacher.chat(_as_chat_messages(_transcript(session_id, user_id)), context)
    _log_turn(session_id, user_id, "teacher", reply)

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

    transcript = _transcript(session_id, user_id)
    context = _teacher_context(session, user_id)
    assessment = state.teacher.assess(transcript, context)

    # Conversation mistakes are free-form signatures Kai identified from the
    # transcript (not necessarily concept ids), so log them explicitly here
    # with that context — apply_session_results below separately logs a
    # mistake_detected event for any entry that DOES match a concept id.
    for signature in assessment["mistakes"]:
        events_log.log(user_id, EventType.MISTAKE_DETECTED, {
            "signature": signature, "concepts": assessment["concepts"],
            "source": "conversation_assessment",
        }, session_id=session_id)

    learner_turns = sum(1 for t in transcript if t["role"] == "learner")
    summary = state.apply_session_results(
        user_id,
        activity_type="conversation",
        concepts=assessment["concepts"],
        mistakes=assessment["mistakes"],
        confidence=assessment["confidence"],
        duration_seconds=now - session["started_at"],
        difficulty=json.loads(session["recommendation"])["difficulty"],
        engaged=learner_turns > 0,  # a session with no learner turns = bounce
        session_id=session_id,
    )
    summary["teacher_notes"] = assessment.get("notes", "")

    return {
        "summary": summary,
        "next_recommendation": state.next_recommendation(user_id, session["minutes"]),
    }
