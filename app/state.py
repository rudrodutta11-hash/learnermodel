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
from teacher_brain import Prediction, SessionOutcome, TeacherBrain

from . import analysis
from . import auth, db
from . import events as events_log
from .analysis import AnalysisMode, analysis_mode_for
from .events import EventType
from .teacher import make_teacher

SUBJECT = "primary"  # MVP: one subject track per learner; the model is already multi-subject

engine = RecommendationEngine()
story_engine = StoryEngine()
brain = TeacherBrain()
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


def streak_days(user_id: str, now: float | None = None) -> int:
    """Consecutive calendar days (UTC) with at least one finished session,
    counting back from today — or from yesterday if today's still open,
    so an unbroken run isn't shown as zero before tonight's session."""
    now = now or time.time()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT CAST(created_at / 86400 AS INTEGER) AS day "
            "FROM session_summaries WHERE user_id = ? ORDER BY day DESC",
            (user_id,),
        ).fetchall()
    days = [r["day"] for r in rows]
    today = int(now // 86400)
    if not days or days[0] < today - 1:
        return 0
    streak, expected = 0, days[0]
    for day in days:
        if day != expected:
            break
        streak += 1
        expected -= 1
    return streak


def home_summary(user_id: str) -> dict:
    """Everything the dashboard needs to feel like a running story:
    where you are (chapter, streak), what just happened (previously),
    and what's waiting tonight (the actual next story beat's hook)."""
    now = time.time()
    memory = relationship_memory(user_id)
    scene = open_story_scene(user_id, now)

    previously = None
    last = memory.get("last_session")
    if last:
        what = ", ".join(c.replace("-", " ") for c in last["concepts"][:3])
        previously = f"You and Kai worked {what}."
        if last["mistakes"]:
            fought = last["mistakes"][0].replace("-", " ")
            previously += f" {fought} put up a fight — Kai hasn't forgotten."

    return {
        "chapter": memory["sessions_together"] + 1,
        "streak_days": streak_days(user_id, now),
        "previously": previously,
        "tonight": {
            "character": scene["character"]["name"],
            "role": scene["character"]["role"],
            "returning": scene["returning"],
            "hook": scene["hook"],
        },
    }


def write_journal(user_id: str, session_id: str | None, note: str) -> None:
    """Append one entry to Kai's teaching journal. INSERT only — like the
    event log, the journal is never rewritten. It holds pedagogical
    observations (how this student learns), not personal-life details."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO kai_journal (user_id, session_id, note, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, session_id, note.strip(), time.time()),
        )


def read_journal(user_id: str, limit: int = 3) -> list[str]:
    """Kai's most recent teaching observations, oldest first so they read
    chronologically in his notes."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT note FROM kai_journal WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [r["note"] for r in reversed(rows)]


# ---- Cost ledger + batch analysis -----------------------------------------

def record_cost(user_id: str, session_id: str | None, activity_type: str,
                analysis_mode: AnalysisMode, ai_calls: int) -> None:
    """Log what a finished activity cost, by activity_type and analysis_mode.
    `billable` reflects whether the live backend actually spends credits."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO activity_costs (user_id, session_id, activity_type, "
            "analysis_mode, ai_calls, billable, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, session_id, activity_type, analysis_mode.value, ai_calls,
             1 if teacher.billable else 0, time.time()),
        )


def cost_report(user_id: str) -> dict:
    """Spend rolled up by activity_type and analysis_mode — the answer to
    'where are the AI calls going?'"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT activity_type, analysis_mode, SUM(ai_calls) AS calls, "
            "COUNT(*) AS sessions, SUM(billable * ai_calls) AS billable_calls "
            "FROM activity_costs WHERE user_id = ? "
            "GROUP BY activity_type, analysis_mode ORDER BY calls DESC",
            (user_id,),
        ).fetchall()
    by = [dict(r) for r in rows]
    return {
        "by_activity": by,
        "total_ai_calls": sum(r["calls"] for r in by),
        "total_billable_calls": sum(r["billable_calls"] for r in by),
        "realtime_calls": sum(r["calls"] for r in by
                              if r["analysis_mode"] == AnalysisMode.REALTIME.value),
        "batch_calls": sum(r["calls"] for r in by
                           if r["analysis_mode"] == AnalysisMode.BATCH.value),
    }


def batch_analyze(user_id: str, session_id: str, *, activity_type: str,
                  concepts: list[str], mistakes: list[str],
                  confidence: float | None, duration_seconds: float,
                  attempted: int | None = None, correct: int | None = None,
                  missed_items: list[dict] | None = None) -> dict:
    """The ONE end-of-session AI call for a batch activity: compact results
    in, full assessment out (notes + journal). Content and grading already
    happened locally; this single call adds the qualitative layer that a
    conversation gets from its live turns."""
    results = analysis.compact_results(
        activity_type=activity_type, concepts=concepts, mistakes=mistakes,
        attempted=attempted, correct=correct, duration_seconds=duration_seconds,
        confidence=confidence, missed_items=missed_items,
    )
    context = {"goal": onboarding(user_id)["goal"], "concepts": concepts,
               "memory": relationship_memory(user_id)}
    assessment = teacher.analyze_session(results, context)
    # Exactly one AI call, whatever the activity's size.
    record_cost(user_id, session_id, activity_type, AnalysisMode.BATCH, ai_calls=1)
    return assessment


# ---- Teacher Brain: predict at session start, resolve at session end -------

def make_predictions(user_id: str, session_id: str) -> list[Prediction]:
    """Generate the Brain's forecast for a session about to begin and store
    each as OPEN. Returns them so the caller can surface them to Kai."""
    profile = load_profile(user_id)
    now = time.time()
    preds = brain.predict(profile, knowledge(user_id), now)
    with db.connect() as conn:
        for p in preds:
            conn.execute(
                "INSERT OR REPLACE INTO teacher_predictions "
                "(id, user_id, session_id, kind, resolved, created_at, data) "
                "VALUES (?, ?, ?, ?, 0, ?, ?)",
                (p.id, user_id, session_id, p.kind.value, p.created_at,
                 json.dumps(p.to_dict())),
            )
    return preds


def open_predictions(user_id: str) -> list[Prediction]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT data FROM teacher_predictions "
            "WHERE user_id = ? AND resolved = 0 ORDER BY id",
            (user_id,),
        ).fetchall()
    return [Prediction.from_dict(json.loads(r["data"])) for r in rows]


def session_outcome(*, engaged: bool, concepts: list[str], mistakes: list[str],
                    difficulty: float, modality: str,
                    confidence: float | None) -> SessionOutcome:
    """Turn a finished session's results into the evidence the Brain resolves
    predictions against. `success` is the fraction of practiced concepts the
    learner got right."""
    missed = set(mistakes)
    got_right = [c for c in concepts if c not in missed]
    success = (len(got_right) / len(concepts)) if concepts else (0.0 if mistakes else 1.0)
    return SessionOutcome(
        engaged=engaged, success=success, difficulty=difficulty, modality=modality,
        concepts=tuple(concepts), mistakes=tuple(mistakes), confidence=confidence,
    )


def resolve_predictions(user_id: str, outcome: SessionOutcome) -> list[Prediction]:
    """At session end, check every open prediction against what happened and
    persist the outcome. This is the step where the teacher finds out whether
    he was right — the heart of getting better over time."""
    now = time.time()
    resolved: list[Prediction] = []
    for p in open_predictions(user_id):
        updated = brain.resolve(p, outcome, now)
        with db.connect() as conn:
            conn.execute(
                "UPDATE teacher_predictions SET resolved = ?, data = ? WHERE id = ?",
                (1 if updated.resolved else 0, json.dumps(updated.to_dict()), updated.id),
            )
        resolved.append(updated)
    return resolved


def prediction_calibration(user_id: str) -> dict:
    """How accurate the Brain's predictions have proven for this learner —
    the growing track record. Eventually the recommendation engine reads
    this to trust some prediction kinds more than others."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT data FROM teacher_predictions "
            "WHERE user_id = ? AND resolved = 1",
            (user_id,),
        ).fetchall()
    decided = [Prediction.from_dict(json.loads(r["data"])) for r in rows]
    return brain.calibration(decided)


def relationship_memory(user_id: str) -> dict:
    """What Kai remembers about this learner across sessions.

    Everything here is pulled from real records (onboarding, session
    summaries, the error model, his own teaching journal) so Kai's
    references to previous sessions are grounded — he is explicitly told
    to never invent a memory, and this is the only memory he gets. It is
    deliberately a TEACHER'S memory: observations about how this person
    learns, not a companion's diary of their life.
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
        first_ts = conn.execute(
            "SELECT MIN(created_at) FROM session_summaries WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]

    memory: dict = {
        "sessions_together": total,
        "level": ob["level"],
        "motivation": ob["motivation"],
        "interests": json.loads(ob["interests"]),
        "days_since_last": None,
        "known_for_days": ((now - first_ts) / 86400.0) if first_ts else None,
        "last_session": None,
        "journal": read_journal(user_id),
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


def recent_activity_types(user_id: str, limit: int = 2) -> tuple[str, ...]:
    """Activity types of the most recent sessions, newest first — feeds
    the engine's variety factor so formats don't repeat on autopilot."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT activity_type FROM session_summaries WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return tuple(r["activity_type"] for r in rows)


def next_recommendation(user_id: str, minutes: float) -> dict:
    """A fresh recommendation reflecting everything learned so far —
    pitched as the next episode, never as a syllabus entry."""
    profile = load_profile(user_id)
    ob = onboarding(user_id)
    rec = engine.recommend(
        profile, knowledge(user_id),
        SessionContext(subject=SUBJECT, available_minutes=minutes, goal=ob["goal"],
                       recent_activity_types=recent_activity_types(user_id),
                       prefer_batch=analysis.cost_prefers_batch()),
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
