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
from recommendation import SessionContext

from . import auth, conversation, db, state

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

    payload = {
        "recommendation": {
            "activity_type": rec.activity_type.value,
            "difficulty": rec.difficulty,
            "concepts": list(rec.concepts),
            "explanation": rec.explanation,
            "expected_outcome": rec.expected_outcome,
            "estimated_minutes": rec.estimated_minutes,
        },
        "session_id": new_session_id(),
    }

    # Conversations are interactive: the client starts a live session via
    # /api/conversation/start instead of receiving pre-built content.
    if rec.activity_type.value == "conversation":
        payload["experience"] = {"title": "Conversation", "interaction": "chat",
                                 "content": ""}
        return payload

    experience = get_experience(rec.activity_type)
    plan = experience.build(rec, profile, state.teacher, goal=onboarding["goal"])
    payload["experience"] = {
        "title": plan.title,
        "content": plan.content,
        "interaction": plan.interaction,
    }
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
    )


@app.get("/api/me")
def me(user_id: str = Depends(state.current_user)):
    profile = state.load_profile(user_id)
    return {"onboarding": state.onboarding(user_id), "learner": profile.summary()}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
