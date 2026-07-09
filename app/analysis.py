"""Analysis modes — the cost architecture for AI involvement.

The principle: not every interaction needs a real-time AI call. Some
activities must adapt live to each turn; most can run on scripted/local
logic or pre-generated content and send ONE compact summary to the AI at
the very end.

    realtime_analysis   AI is in the loop every turn. Expensive by nature —
                        reserve it for what genuinely needs live adaptation:
                        Kai conversations, live writing feedback, calls.

    batch_analysis      The interaction runs locally (client-side grading,
                        pre-generated content); the AI is called exactly
                        ONCE at the end, on compact structured results —
                        never the full raw history. It produces the session
                        summary, mistakes, mastery signals, journal entry,
                        and next-recommendation signals in that single call.
                        Use for flashcards, quizzes, drills, reading checks,
                        structured lessons.

One batch call replaces the many small calls a naive design would make
(grading each card, reacting to each answer). That is the saving.
"""
from __future__ import annotations

import os
from enum import Enum

from learner_model.events import Modality


class AnalysisMode(str, Enum):
    REALTIME = "realtime_analysis"
    BATCH = "batch_analysis"


# The mode each activity type runs in. Anything needing live adaptation is
# realtime; everything structured/local is batch.
ANALYSIS_MODE: dict[Modality, AnalysisMode] = {
    Modality.CONVERSATION: AnalysisMode.REALTIME,
    Modality.WRITING: AnalysisMode.REALTIME,
    Modality.CALL: AnalysisMode.REALTIME,
    Modality.FLASHCARD: AnalysisMode.BATCH,
    Modality.QUIZ: AnalysisMode.BATCH,
    Modality.MINI_LESSON: AnalysisMode.BATCH,
    Modality.STORY: AnalysisMode.BATCH,
    Modality.GAME: AnalysisMode.BATCH,
    Modality.VIDEO: AnalysisMode.BATCH,
}


def analysis_mode_for(activity_type: str) -> AnalysisMode:
    """The analysis mode for an activity type string. Unknown types default
    to batch — the cheap side is the safe default."""
    try:
        return ANALYSIS_MODE.get(Modality(activity_type), AnalysisMode.BATCH)
    except ValueError:
        return AnalysisMode.BATCH


def cost_prefers_batch() -> bool:
    """Whether the current cost policy should steer toward batch activities.

    Tied to AI_MODE: in `mock` (free dev) and `cheap` (Haiku) we prefer
    batch when real-time adaptation isn't necessary; in `premium` we let the
    recommendation stand on pedagogy alone. This only nudges format choice —
    it never suppresses a conversation the learner explicitly starts."""
    return os.environ.get("AI_MODE", "mock").lower() in ("mock", "cheap")


def compact_results(
    *,
    activity_type: str,
    concepts: list[str],
    mistakes: list[str],
    attempted: int | None = None,
    correct: int | None = None,
    duration_seconds: float = 0.0,
    confidence: float | None = None,
    missed_items: list[dict] | None = None,
    max_items: int = 8,
) -> dict:
    """Shape a finished batch activity into the compact payload the one
    end-of-session AI call receives. Deliberately aggregate: counts, the
    concept list, and only the MISSED items (capped) — never every card,
    never the full raw interaction."""
    attempted = attempted if attempted is not None else len(concepts)
    correct = correct if correct is not None else max(0, len(concepts) - len(mistakes))
    return {
        "activity_type": activity_type,
        "concepts": concepts,
        "mistakes": mistakes,
        "counts": {"attempted": attempted, "correct": correct},
        "duration_seconds": round(duration_seconds, 1),
        "confidence": confidence,
        # Only what the AI needs to write good notes: the stumbles, capped.
        "missed_items": (missed_items or [])[:max_items],
    }
