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
    # Activity types of the most recent sessions, newest first. Used for
    # the variety factor: same format twice in a row must EARN its repeat.
    recent_activity_types: tuple[str, ...] = ()
    # Cost policy: when true (cheap/mock AI modes), gently prefer activities
    # that need only one end-of-session AI call over realtime ones — used
    # only when live adaptation isn't essential. Never a hard exclusion.
    prefer_batch: bool = False


# Activity types that need a live AI call every turn (realtime_analysis).
# Under prefer_batch they take a small cost penalty — enough to break ties
# toward cheaper batch formats, never enough to suppress a format the
# learner clearly needs.
REALTIME_ACTIVITIES: frozenset[Modality] = frozenset(
    {Modality.CONVERSATION, Modality.WRITING, Modality.CALL}
)
BATCH_PREFERENCE_FACTOR = 0.8


# A format repeated back-to-back is penalized (not banned — if flashcards
# are overwhelmingly right tonight, flashcards still win). Two sessions
# ago costs less; further back costs nothing. Variety is pedagogy, not
# cosmetics: interleaving formats strengthens retrieval, and a product
# that opens on the same card every night stops feeling authored.
REPEAT_PENALTY = (0.6, 0.85)


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
                    * self._variety_factor(activity, context)
                    * self._cost_factor(activity, context)
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
            # Learner-facing copy: story language only, never syllabus
            # language ("lesson", "exercise", "review"...) — the episode
            # preview in recommendation/preview.py is built on top of this
            # and tests enforce the ban across both.
            explanation=(
                f"{need.reason} Kai's set it up the way you do this best — "
                f"{minutes:.0f} minutes, in and out."
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
                        f"{len(review_queue)} thing(s) you've already won are "
                        "quietly slipping away."
                    ),
                    outcome="Catch them at the door — keep what's yours.",
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
                        f"An old enemy — {top.signature.replace('-', ' ')} — "
                        f"has beaten you {top.count} times."
                    ),
                    outcome="The rematch. This one ends differently.",
                )
            )

        frontier = knowledge.frontier(profile.memory, now)
        needs.append(
            _Need(
                kind="new_material",
                value=0.55 if not review_queue else 0.35,
                concepts=tuple(c.id for c in frontier),
                reason="Everything behind you is solid — time to open the next door."
                if not review_queue
                else "A little new territory keeps the story moving.",
                outcome="New ground, claimed.",
            )
        )
        return needs

    @staticmethod
    def _cost_factor(activity: Modality, context: SessionContext) -> float:
        if context.prefer_batch and activity in REALTIME_ACTIVITIES:
            return BATCH_PREFERENCE_FACTOR
        return 1.0

    @staticmethod
    def _variety_factor(activity: Modality, context: SessionContext) -> float:
        for position, recent in enumerate(context.recent_activity_types):
            if recent == activity.value and position < len(REPEAT_PENALTY):
                return REPEAT_PENALTY[position]
        return 1.0

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
