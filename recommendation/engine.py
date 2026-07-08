"""The Recommendation Engine.

Its only responsibility: answer one question —

    "What is the highest-impact thing this learner can do
     in the next few minutes?"

It decides WHAT should happen (activity type, difficulty, concepts, and
why). It never generates content — delivery belongs to the Experience
Engine. That boundary is what lets us add new experience types (voice
calls, generated video, games) without ever touching this file.

Scoring model
-------------
The engine generates *needs* (review what's decaying, fix recurring
errors, learn new material), pairs each need with the activity types that
can serve it, and scores every pair:

    score = need_value                      # how much this need matters now
          * modality_effectiveness          # does this format work for THIS learner
          * challenge_fit                   # right difficulty for THIS learner
          * time_fit                        # respects available time + attention span

Modality effectiveness uses Thompson sampling, so the engine keeps
exploring formats it hasn't given a fair chance yet.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from learner_model.events import Modality
from learner_model.knowledge import KnowledgeState
from learner_model.profile import LearnerProfile

# Which activity types can serve which need. Adding an experience type is
# a registry entry here + an Experience implementation — nothing else.
NEED_ACTIVITIES: dict[str, tuple[Modality, ...]] = {
    "review": (Modality.FLASHCARD, Modality.QUIZ),
    "fix_errors": (Modality.QUIZ, Modality.WRITING, Modality.CONVERSATION),
    "new_material": (
        Modality.MINI_LESSON,
        Modality.STORY,
        Modality.CONVERSATION,
        Modality.FLASHCARD,
    ),
}

# Nominal minutes to do one concept justice in each format.
MINUTES_PER_CONCEPT: dict[Modality, float] = {
    Modality.FLASHCARD: 0.5,
    Modality.QUIZ: 1.0,
    Modality.WRITING: 4.0,
    Modality.CONVERSATION: 3.0,
    Modality.MINI_LESSON: 3.0,
    Modality.STORY: 2.0,
    Modality.VIDEO: 3.0,
    Modality.GAME: 2.0,
    Modality.CALL: 5.0,
}

MIN_ACTIVITY_MINUTES = 2.0


@dataclass(frozen=True)
class SessionContext:
    """Everything situational the engine needs: the learner supplies only this."""

    subject: str
    available_minutes: float
    goal: str = ""
    now_ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Recommendation:
    """The engine's full answer — the contract consumed by the Experience Engine."""

    activity_type: Modality
    difficulty: float
    concepts: tuple[str, ...]        # concept ids; empty means "teacher's choice"
    explanation: str                 # why this, why now — shown to the learner
    expected_outcome: str
    estimated_minutes: float
    need: str                        # review | fix_errors | new_material
    score: float


@dataclass(frozen=True)
class _Need:
    kind: str
    value: float          # urgency in [0, 1]
    concepts: tuple[str, ...]
    reason: str
    outcome: str


class RecommendationEngine:
    def recommend(
        self,
        profile: LearnerProfile,
        knowledge: KnowledgeState,
        context: SessionContext,
    ) -> Recommendation:
        needs = self._gather_needs(profile, knowledge, context)
        best: tuple[float, _Need, Modality] | None = None

        for need in needs:
            for activity in NEED_ACTIVITIES[need.kind]:
                score = (
                    need.value
                    * profile.modality.explore_score(activity)
                    * max(0.1, profile.challenge.fit(self._difficulty(profile)))
                    * self._time_fit(profile, context, activity)
                )
                if best is None or score > best[0]:
                    best = (score, need, activity)

        assert best is not None  # new_material need always exists
        score, need, activity = best

        minutes = profile.focus.recommended_activity_minutes(context.available_minutes)
        max_concepts = max(1, int(minutes / MINUTES_PER_CONCEPT[activity]))
        concepts = need.concepts[:max_concepts]

        return Recommendation(
            activity_type=activity,
            difficulty=round(self._difficulty(profile), 2),
            concepts=concepts,
            explanation=(
                f"{need.reason} A {activity.value.replace('_', ' ')} is the format "
                f"that works best for you right now, sized to "
                f"{minutes:.0f} minutes."
            ),
            expected_outcome=need.outcome,
            estimated_minutes=round(minutes, 1),
            need=need.kind,
            score=round(score, 4),
        )

    # -- internals ---------------------------------------------------------

    def _gather_needs(
        self,
        profile: LearnerProfile,
        knowledge: KnowledgeState,
        context: SessionContext,
    ) -> list[_Need]:
        now = context.now_ts
        needs: list[_Need] = []

        review_queue = knowledge.review_queue(profile.memory, now)
        if review_queue:
            urgency = 1.0 - (
                sum(s.retrievability for s in review_queue) / len(review_queue)
            )
            needs.append(
                _Need(
                    kind="review",
                    value=0.5 + 0.5 * urgency,
                    concepts=tuple(s.concept.id for s in review_queue),
                    reason=(
                        f"{len(review_queue)} concept(s) are fading from memory "
                        "and due for review."
                    ),
                    outcome="Reinforce fading memories before they need relearning.",
                )
            )

        recurring = profile.errors.recurring(now, subject=context.subject)
        if recurring:
            top = recurring[0]
            needs.append(
                _Need(
                    kind="fix_errors",
                    value=min(1.0, 0.4 + 0.15 * top.count),
                    concepts=tuple(r.signature for r in recurring[:3]),
                    reason=(
                        f"You've repeated the same mistake ({top.signature}) "
                        f"{top.count} times."
                    ),
                    outcome="Break a recurring error pattern with targeted practice.",
                )
            )

        frontier = knowledge.frontier(profile.memory, now)
        needs.append(
            _Need(
                kind="new_material",
                value=0.55 if not review_queue else 0.35,
                concepts=tuple(c.id for c in frontier),
                reason="You're on top of your reviews — time to learn something new."
                if not review_queue
                else "Mixing in new material keeps momentum.",
                outcome="Expand into new territory at the right difficulty.",
            )
        )
        return needs

    @staticmethod
    def _difficulty(profile: LearnerProfile) -> float:
        return profile.challenge.target_difficulty()

    @staticmethod
    def _time_fit(
        profile: LearnerProfile, context: SessionContext, activity: Modality
    ) -> float:
        """Penalize formats that can't do useful work in the time available."""
        minutes = profile.focus.recommended_activity_minutes(context.available_minutes)
        needed = max(MIN_ACTIVITY_MINUTES, MINUTES_PER_CONCEPT[activity])
        if minutes >= needed:
            return 1.0
        return max(0.05, minutes / needed)
