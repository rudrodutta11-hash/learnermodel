"""Knowledge State: what this learner knows right now, and what's decaying.

Distinct from the LearnerProfile on purpose:

  - LearnerProfile  = HOW the person learns (permanent, subject-agnostic)
  - KnowledgeState  = WHAT they currently know (per subject, perishable)

KnowledgeState is a *view* computed over the memory model plus the
subject's concept graph — it holds no learning traits of its own. Concepts
are mastered, weak, or unseen; each tracked concept has a review-due time
derived from the learner's personal forgetting curve.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .memory import MemoryModel

MASTERY_RETRIEVABILITY = 0.85
MASTERY_MIN_REPS = 3
WEAK_RETRIEVABILITY = 0.6
REVIEW_DUE_RETRIEVABILITY = 0.7


@dataclass(frozen=True)
class Concept:
    """A knowledge atom declared by a subject's content pack."""

    id: str
    subject: str
    name: str
    difficulty: float = 0.5
    prerequisites: tuple[str, ...] = ()   # concept ids within the same subject

    @property
    def key(self) -> str:
        return f"{self.subject}:{self.id}"


@dataclass
class ConceptStatus:
    concept: Concept
    retrievability: float
    repetitions: int
    lapses: int
    review_due_ts: float | None  # None = not yet learned

    @property
    def mastered(self) -> bool:
        return (
            self.repetitions >= MASTERY_MIN_REPS
            and self.retrievability >= MASTERY_RETRIEVABILITY
        )

    @property
    def weak(self) -> bool:
        return self.repetitions > 0 and self.retrievability < WEAK_RETRIEVABILITY


@dataclass
class KnowledgeState:
    """The learner's current knowledge in one subject."""

    subject: str
    concepts: dict[str, Concept] = field(default_factory=dict)  # by concept id

    def add_concepts(self, concepts: list[Concept]) -> None:
        for c in concepts:
            if c.subject != self.subject:
                raise ValueError(f"Concept {c.id} belongs to {c.subject}, not {self.subject}")
            self.concepts[c.id] = c

    def status(self, memory: MemoryModel, now_ts: float) -> list[ConceptStatus]:
        out = []
        for concept in self.concepts.values():
            item = memory.items.get(concept.key)
            if item is None:
                out.append(ConceptStatus(concept, 0.0, 0, 0, None))
                continue
            out.append(
                ConceptStatus(
                    concept=concept,
                    retrievability=item.retrievability(now_ts),
                    repetitions=item.repetitions,
                    lapses=item.lapses,
                    review_due_ts=_due_time(
                        item.last_review_ts, item.half_life_hours
                    ),
                )
            )
        return out

    def mastered(self, memory: MemoryModel, now_ts: float) -> list[ConceptStatus]:
        return [s for s in self.status(memory, now_ts) if s.mastered]

    def weak(self, memory: MemoryModel, now_ts: float) -> list[ConceptStatus]:
        return sorted(
            (s for s in self.status(memory, now_ts) if s.weak),
            key=lambda s: s.retrievability,
        )

    def review_queue(self, memory: MemoryModel, now_ts: float) -> list[ConceptStatus]:
        """Learned concepts whose retrievability has fallen to review range."""
        due = [
            s
            for s in self.status(memory, now_ts)
            if s.repetitions > 0 and s.retrievability < REVIEW_DUE_RETRIEVABILITY
        ]
        return sorted(due, key=lambda s: s.retrievability)

    def frontier(self, memory: MemoryModel, now_ts: float) -> list[Concept]:
        """Unseen concepts whose prerequisites are mastered — what to learn next."""
        by_id = {s.concept.id: s for s in self.status(memory, now_ts)}
        out = []
        for concept in self.concepts.values():
            seen = by_id[concept.id].repetitions > 0
            if seen:
                continue
            if all(
                pid in by_id and by_id[pid].mastered for pid in concept.prerequisites
            ):
                out.append(concept)
        return sorted(out, key=lambda c: c.difficulty)


def _due_time(last_review_ts: float, half_life_hours: float) -> float:
    """When retrievability will hit the review threshold: solve R(t) = threshold."""
    hours = half_life_hours * -math.log2(REVIEW_DUE_RETRIEVABILITY)
    return last_review_ts + hours * 3600.0
