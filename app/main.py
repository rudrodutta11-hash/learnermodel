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
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from experience import get_experience
from learner_model import InteractionEvent, LearnerProfile, Modality, new_session_id
from learner_model.history import SessionHistory
from learner_model.knowledge import Concept, KnowledgeState
from learner_model.store import load_profile, save_profile
from recommendation import RecommendationEngine, SessionContext

from . import auth, db
from .teacher import make_teacher

SUBJECT = "primary"  # MVP: one subject track per learner; the model is already multi-subject
STATIC_DIR = Path(__file__).parent / "static"

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="LearnerModel MVP", lifespan=_lifespan)
engine = RecommendationEngine()
teacher = make_teacher()


# -- auth plumbing -----------------------------------------------------------

def current_user(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    user_id = auth.user_id_for_token(token) if token else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id


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


# -- persistence helpers -----------------------------------------------------

def _load_profile(user_id: str) -> LearnerProfile:
    try:
        return load_profile(user_id, db.PROFILES_DIR)
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail="Complete onboarding first")


def _knowledge(user_id: str) -> KnowledgeState:
    ks = KnowledgeState(subject=SUBJECT)
    path = db.DATA_DIR / "concepts" / f"{user_id}.json"
    if path.exists():
        ks.add_concepts([
            Concept(subject=SUBJECT, **c) for c in json.loads(path.read_text())
        ])
    return ks


def _save_concepts(user_id: str, ks: KnowledgeState) -> None:
    path = db.DATA_DIR / "concepts" / f"{user_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([
        {"id": c.id, "name": c.name, "difficulty": c.difficulty,
         "prerequisites": list(c.prerequisites)}
        for c in ks.concepts.values()
    ]))


def _onboarding(user_id: str) -> dict:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM onboarding WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail="Complete onboarding first")
    return dict(row)


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
def onboard(data: Onboarding, user_id: str = Depends(current_user)):
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
def recommendation(minutes: float, user_id: str = Depends(current_user)):
    """The dashboard's only question was 'how much time do you have?'
    — this endpoint answers with exactly ONE next thing to do."""
    profile = _load_profile(user_id)
    onboarding = _onboarding(user_id)
    knowledge = _knowledge(user_id)

    context = SessionContext(
        subject=SUBJECT, available_minutes=minutes, goal=onboarding["goal"]
    )
    rec = engine.recommend(profile, knowledge, context)

    experience = get_experience(rec.activity_type)
    plan = experience.build(rec, profile, teacher, goal=onboarding["goal"])

    return {
        "recommendation": {
            "activity_type": rec.activity_type.value,
            "difficulty": rec.difficulty,
            "concepts": list(rec.concepts),
            "explanation": rec.explanation,
            "expected_outcome": rec.expected_outcome,
            "estimated_minutes": rec.estimated_minutes,
        },
        "experience": {
            "title": plan.title,
            "content": plan.content,
            "interaction": plan.interaction,
        },
        "session_id": new_session_id(),
    }


@app.post("/api/session-summary")
def session_summary(data: SessionSummary, user_id: str = Depends(current_user)):
    """The single write path after every activity: feeds the learner
    model, updates the knowledge state, and records the summary."""
    profile = _load_profile(user_id)
    knowledge = _knowledge(user_id)
    history = SessionHistory(db.HISTORY_DIR)
    session_id = new_session_id()
    now = time.time()
    modality = Modality(data.activity_type)
    mistakes = set(data.mistakes)

    for concept_id in data.concepts:
        if concept_id not in knowledge.concepts:
            knowledge.add_concepts([Concept(
                id=concept_id, subject=SUBJECT,
                name=concept_id.replace("-", " "),
                difficulty=data.difficulty,
            )])
        event = InteractionEvent(
            learner_id=user_id,
            subject=SUBJECT,
            item_id=concept_id,
            modality=modality,
            correct=concept_id not in mistakes,
            confidence=data.confidence,
            difficulty=data.difficulty,
            duration_seconds=data.duration_seconds / max(1, len(data.concepts)),
            engaged=data.engaged,
            error_signature=concept_id if concept_id in mistakes else None,
            timestamp=now,
            session_id=session_id,
        )
        history.append(event)
        profile.update(event)

    save_profile(profile, db.PROFILES_DIR)
    _save_concepts(user_id, knowledge)

    # Estimated mastery + next review, straight from the memory model.
    statuses = {s.concept.id: s for s in knowledge.status(profile.memory, now)}
    practiced = [statuses[c] for c in data.concepts if c in statuses]
    mastery = (
        sum(s.retrievability for s in practiced) / len(practiced) if practiced else None
    )
    review_due = min(
        (s.review_due_ts for s in practiced if s.review_due_ts), default=None
    )

    with db.connect() as conn:
        conn.execute(
            "INSERT INTO session_summaries (user_id, created_at, activity_type, "
            "concepts, mistakes, confidence, estimated_mastery, review_due_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, now, data.activity_type, json.dumps(data.concepts),
             json.dumps(data.mistakes), data.confidence, mastery, review_due),
        )

    return {
        "estimated_mastery": round(mastery, 3) if mastery is not None else None,
        "recommended_review_at": review_due,
        "learner_summary": profile.summary(),
    }


@app.get("/api/me")
def me(user_id: str = Depends(current_user)):
    profile = _load_profile(user_id)
    return {"onboarding": _onboarding(user_id), "learner": profile.summary()}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
