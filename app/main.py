"""LearnerModel MVP — the adaptive AI teacher, Version 1.

The product loop:
  register → onboard → "How much time do you have?" → ONE recommendation
  → experience delivered by the AI teacher → session summary → the
  learner model gets smarter → repeat.

No curriculum anywhere. The Recommendation Engine decides WHAT from the
learner model + knowledge state + history; the Experience Engine and the
Teacher decide HOW and generate content dynamically.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from experience import get_experience
from learner_model import LearnerProfile, new_session_id
from learner_model.store import save_profile
from recommendation import SessionContext, episode_preview

from . import auth, conversation, db, state
from .events import EventType
from . import events as events_log

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="LearnerModel MVP", lifespan=_lifespan)
app.include_router(conversation.router)


# -- request/response models -------------------------------------------------

class Credentials(BaseModel):
    email: str
    password: str = Field(min_length=8)


class Onboarding(BaseModel):
    goal: str
    level: str
    interests: list[str] = []
    weekly_minutes: int = 60
    motivation: str = ""


class SessionSummary(BaseModel):
    activity_type: str
    concepts: list[str]                 # concept ids practiced
    mistakes: list[str] = []            # error signatures for concepts missed
    confidence: float | None = None     # 0..1 self-report
    engaged: bool = True
    duration_seconds: float = 0.0
    difficulty: float = 0.5
    session_id: str | None = None       # correlates back to /api/recommendation


# -- routes -------------------------------------------------------------------

@app.post("/api/register")
def register(creds: Credentials):
    try:
        return {"token": auth.register(creds.email, creds.password)}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/login")
def login(creds: Credentials):
    try:
        return {"token": auth.login(creds.email, creds.password)}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@app.post("/api/onboarding")
def onboard(data: Onboarding, user_id: str = Depends(state.current_user)):
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO onboarding "
            "(user_id, goal, level, interests, weekly_minutes, motivation) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, data.goal, data.level, json.dumps(data.interests),
             data.weekly_minutes, data.motivation),
        )
    # Create the initial learner profile — universal priors, ready to adapt.
    save_profile(LearnerProfile(learner_id=user_id), db.PROFILES_DIR)
    return {"status": "ok"}


@app.get("/api/recommendation")
def recommendation(minutes: float, user_id: str = Depends(state.current_user)):
    """The dashboard's only question was 'how much time do you have?'
    — this endpoint answers with exactly ONE next thing to do."""
    profile = state.load_profile(user_id)
    onboarding = state.onboarding(user_id)
    knowledge = state.knowledge(user_id)

    context = SessionContext(
        subject=state.SUBJECT, available_minutes=minutes, goal=onboarding["goal"]
    )
    rec = state.engine.recommend(profile, knowledge, context)
    session_id = new_session_id()
    memory = state.relationship_memory(user_id)

    payload = {
        "recommendation": {
            "activity_type": rec.activity_type.value,
            "difficulty": rec.difficulty,
            "concepts": list(rec.concepts),
            "explanation": rec.explanation,
            "expected_outcome": rec.expected_outcome,
            "estimated_minutes": rec.estimated_minutes,
        },
        # The learner-facing pitch: an episode card, not a syllabus entry.
        "preview": episode_preview(
            need=rec.need, concepts=list(rec.concepts),
            minutes=rec.estimated_minutes, goal=onboarding["goal"],
            memory=memory,
        ),
        "session_id": session_id,
    }

    # Conversations are interactive: the client starts a live session via
    # /api/conversation/start, which logs its own recommendation_created /
    # activity_started against a session_id that persists through /end —
    # so this preview intentionally doesn't log those events.
    if rec.activity_type.value == "conversation":
        payload["experience"] = {"title": "Conversation", "interaction": "chat",
                                 "content": ""}
        return payload

    events_log.log(user_id, EventType.RECOMMENDATION_CREATED, {
        "activity_type": rec.activity_type.value, "difficulty": rec.difficulty,
        "concepts": list(rec.concepts), "need": rec.need,
        "explanation": rec.explanation, "estimated_minutes": rec.estimated_minutes,
    }, session_id=session_id)

    experience = get_experience(rec.activity_type)
    plan = experience.build(rec, profile, state.teacher, goal=onboarding["goal"],
                            memory=memory)
    payload["experience"] = {
        "title": plan.title,
        "content": plan.content,
        "interaction": plan.interaction,
    }

    # Content is delivered immediately in this MVP (no separate "user
    # clicked start" step for non-interactive formats).
    events_log.log(user_id, EventType.ACTIVITY_STARTED, {
        "activity_type": rec.activity_type.value,
    }, session_id=session_id)

    return payload


@app.post("/api/session-summary")
def session_summary(data: SessionSummary, user_id: str = Depends(state.current_user)):
    """The write path after every non-interactive activity."""
    return state.apply_session_results(
        user_id,
        activity_type=data.activity_type,
        concepts=data.concepts,
        mistakes=data.mistakes,
        confidence=data.confidence,
        duration_seconds=data.duration_seconds,
        difficulty=data.difficulty,
        engaged=data.engaged,
        session_id=data.session_id,
    )


@app.get("/api/events")
def event_log(session_id: str | None = None, user_id: str = Depends(state.current_user)):
    """The learner's full append-only event trail — the replay surface
    future model versions read from. Never mutated, only ever grown."""
    return {"events": events_log.replay(user_id, session_id)}


@app.get("/api/me")
def me(user_id: str = Depends(state.current_user)):
    profile = state.load_profile(user_id)
    return {"onboarding": state.onboarding(user_id), "learner": profile.summary()}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
