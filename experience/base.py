"""The Experience Engine: delivery, decoupled from decision.

The Recommendation Engine says WHAT should happen; an Experience turns
that into something the learner actually does. New experience types
(roleplay, generated video, live voice call) are added by registering a
class here — the Recommendation Engine never changes.

Every experience speaks through the Teacher abstraction, so the learner
always hears one consistent voice regardless of format — and when that
voice becomes literal (TTS, phone calls), only the Teacher's transport
changes, not the experiences.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from learner_model.events import Modality
from learner_model.profile import LearnerProfile
from recommendation.engine import Recommendation


class Teacher(Protocol):
    """Minimal contract every experience relies on. Implementations may be
    LLM-backed, scripted, or (later) voice — experiences don't care."""

    def generate(self, instruction: str, context: dict[str, Any]) -> str: ...


@dataclass(frozen=True)
class ExperiencePlan:
    """A renderable, deliverable learning experience."""

    activity_type: Modality
    title: str
    content: str                    # teacher-generated body (lesson, prompts, cards…)
    interaction: str                # how the learner responds: "answer", "write", "chat"
    concepts: tuple[str, ...]
    difficulty: float
    estimated_minutes: float
    metadata: dict[str, Any] = field(default_factory=dict)


class Experience(Protocol):
    activity_type: Modality

    def build(
        self,
        recommendation: Recommendation,
        profile: LearnerProfile,
        teacher: Teacher,
        goal: str,
    ) -> ExperiencePlan: ...


_REGISTRY: dict[Modality, Experience] = {}


def register_experience(experience: Experience) -> Experience:
    """Register an experience type. This one line is the entire cost of
    adding a new delivery format to the platform."""
    _REGISTRY[experience.activity_type] = experience
    return experience


def get_experience(activity_type: Modality) -> Experience:
    try:
        return _REGISTRY[activity_type]
    except KeyError:
        raise KeyError(
            f"No experience registered for {activity_type.value!r}. "
            f"Available: {sorted(m.value for m in _REGISTRY)}"
        ) from None
