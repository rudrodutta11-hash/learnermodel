"""Shared application state and the single write path into the learner model.

Both the generic session-summary endpoint and the conversation experience
funnel their results through `apply_session_results` — one code path turns
"what happened in a session" into learner-model updates, whatever the
experience type. New experiences (voice calls included) reuse it as-is.
"""
from __future__ import annotations

import json
import time

from fastapi import Depends, Header, HTTPException

from learner_model import InteractionEvent, LearnerProfile, Modality, new_session_id
from learner_model.history import SessionHistory
from learner_model.knowledge import Concept, KnowledgeState
from learner_model.store import load_profile as _load, save_profile
from recommendation import RecommendationEngine, SessionContext, episode_preview
from story import StoryEngine, character_by_id
from story.store import load_story as _load_story, save_story as _save_story

from . import auth, db
from . import events as events_log
from .events import EventType
from .teacher import make_teacher

SUBJECT = "primary"  # MVP: one subject track per learner; the model is already multi-subject

engine = RecommendationEngine()
story_engine = StoryEngine()
teacher = make_teacher()


def current_user(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    user_id = auth.user_id_for_token(token) if token else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id


def load_profile(user_id: str) -> LearnerProfile:
    try:
        return _load(user_id, db.PROFILES_DIR)
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail="Complete onboarding first")


def knowledge(user_id: str) -> KnowledgeState:
    ks = KnowledgeState(subject=SUBJECT)
    path = db.DATA_DIR / "concepts" / f"{user_id}.json"
    if path.exists():
        ks.add_concepts([
            Concept(subject=SUBJECT, **c) for c in json.loads(path.read_text())
        ])
    return ks


def save_concepts(user_id: str, ks: KnowledgeState) -> None:
    path = db.DATA_DIR / "concepts" / f"{user_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([
        {"id": c.id, "name": c.name, "difficulty": c.difficulty,
         "prerequisites": list(c.prerequisites)}
        for c in ks.concepts.values()
    ]))


def onboarding(user_id: str) -> dict:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM onboarding WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail="Complete onboarding first")
    return dict(row)


def open_story_scene(user_id: str, now: float) -> dict:
    """Pick tonight's recurring character and stage the current beat of
    their story. Read-only — the thread only advances when the session
    ends (see advance_story)."""
    story = _load_story(user_id, db.STORY_DIR)
    character, beat_index, scene = story_engine.next_scene(story, SUBJECT, now)
    scene["character_id"] = character.id
    scene["beat_index"] = beat_index
    return scene


def advance_story(user_id: str, character_id: str, beat_index: int,
                  now: float, extra_memory: str = "") -> None:
    """After the session, move this character's story one beat forward and
    record what the learner will remember of it."""
    if not character_by_id(SUBJECT, character_id):
        return
    story = _load_story(user_id, db.STORY_DIR)
    story_engine.advance(story, SUBJECT, character_id, beat_index, now,
                         extra_memory=extra_memory)
    _save_story(story, db.STORY_DIR)


def relationship_memory(user_id: str) -> dict:
    """What Kai remembers about this learner across sessions.

    Everything here is pulled from real records (onboarding, session
    summaries, the error model) so Kai's references to previous sessions
    are grounded — he is explicitly told to never invent a memory, and
    this is the only memory he gets.
    """
    ob = onboarding(user_id)
    profile = load_profile(user_id)
    now = time.time()

    with db.connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM session_summaries WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        last_row = conn.execute(
            "SELECT * FROM session_summaries WHERE user_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    memory: dict = {
        "sessions_together": total,
        "level": ob["level"],
        "motivation": ob["motivation"],
        "interests": json.loads(ob["interests"]),
        "days_since_last": None,
        "last_session": None,
        "recurring_mistakes": [
            (r.signature, r.count)
            for r in profile.errors.recurring(now, subject=SUBJECT)[:3]
        ],
    }
    if last_row is not None:
        memory["days_since_last"] = (now - last_row["created_at"]) / 86400.0
        memory["last_session"] = {
            "activity_type": last_row["activity_type"],
            "concepts": json.loads(last_row["concepts"]),
            "mistakes": json.loads(last_row["mistakes"]),
        }
    return memory


def next_recommendation(user_id: str, minutes: float) -> dict:
    """A fresh recommendation reflecting everything learned so far —
    pitched as the next episode, never as a syllabus entry."""
    profile = load_profile(user_id)
    ob = onboarding(user_id)
    rec = engine.recommend(
        profile, knowledge(user_id),
        SessionContext(subject=SUBJECT, available_minutes=minutes, goal=ob["goal"]),
    )
    return {
        "activity_type": rec.activity_type.value,
        "difficulty": rec.difficulty,
        "concepts": list(rec.concepts),
        "explanation": rec.explanation,
        "expected_outcome": rec.expected_outcome,
        "estimated_minutes": rec.estimated_minutes,
        "preview": episode_preview(
            need=rec.need, concepts=list(rec.concepts),
            minutes=rec.estimated_minutes, goal=ob["goal"],
            memory=relationship_memory(user_id),
        ),
    }


def apply_session_results(
    user_id: str,
    *,
    activity_type: str,
    concepts: list[str],
    mistakes: list[str],
    confidence: float | None,
    duration_seconds: float,
    difficulty: float,
    engaged: bool = True,
    session_id: str | None = None,
) -> dict:
    """The single write path after ANY completed activity: feeds the
    learner model, updates the knowledge state, records the summary, logs
    the append-only event trail, and returns mastery + next review date."""
    profile = load_profile(user_id)
    ks = knowledge(user_id)
    history = SessionHistory(db.HISTORY_DIR)
    session_id = session_id or new_session_id()
    now = time.time()
    modality = Modality(activity_type)
    missed = set(mistakes)

    for concept_id in concepts:
        if concept_id not in ks.concepts:
            ks.add_concepts([Concept(
                id=concept_id, subject=SUBJECT,
                name=concept_id.replace("-", " "),
                difficulty=difficulty,
            )])
        event = InteractionEvent(
            learner_id=user_id,
            subject=SUBJECT,
            item_id=concept_id,
            modality=modality,
            correct=concept_id not in missed,
            confidence=confidence,
            difficulty=difficulty,
            duration_seconds=duration_seconds / max(1, len(concepts)),
            engaged=engaged,
            error_signature=concept_id if concept_id in missed else None,
            timestamp=now,
            session_id=session_id,
        )
        history.append(event)
        profile.update(event)
        if concept_id in missed:
            events_log.log(user_id, EventType.MISTAKE_DETECTED, {
                "concept": concept_id, "activity_type": activity_type,
            }, session_id=session_id)

    save_profile(profile, db.PROFILES_DIR)
    save_concepts(user_id, ks)

    statuses = {s.concept.id: s for s in ks.status(profile.memory, now)}
    practiced = [statuses[c] for c in concepts if c in statuses]
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
            (user_id, now, activity_type, json.dumps(concepts),
             json.dumps(mistakes), confidence, mastery, review_due),
        )

    events_log.log(user_id, EventType.ACTIVITY_COMPLETED, {
        "activity_type": activity_type, "concepts": concepts,
        "duration_seconds": duration_seconds, "engaged": engaged,
    }, session_id=session_id)
    events_log.log(user_id, EventType.SESSION_SUMMARIZED, {
        "concepts_practiced": concepts, "mistakes": mistakes,
        "confidence": confidence, "estimated_mastery": mastery,
        "recommended_review_at": review_due,
    }, session_id=session_id)
    events_log.log(user_id, EventType.LEARNER_PROFILE_UPDATED, profile.summary(),
                   session_id=session_id)

    return {
        "session_id": session_id,
        "concepts_practiced": concepts,
        "mistakes": mistakes,
        "confidence": confidence,
        "estimated_mastery": round(mastery, 3) if mastery is not None else None,
        "recommended_review_at": review_due,
        "learner_summary": profile.summary(),
    }
