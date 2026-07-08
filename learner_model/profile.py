"""LearnerProfile: the product.

One object per human. It aggregates every dimension of *how* this person
learns — memory, modality response, focus, calibration, error patterns,
challenge tolerance — and is deliberately ignorant of any subject's
content. Subjects come and go; the profile persists and compounds.

Six months of Spanish teaches the profile. Day one of History spends it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .challenge import ChallengeModel
from .confidence import ConfidenceModel
from .errors import ErrorModel
from .events import InteractionEvent
from .focus import FocusModel
from .memory import MemoryModel
from .modality import ModalityModel


@dataclass
class LearnerProfile:
    learner_id: str
    memory: MemoryModel = field(default_factory=MemoryModel)
    modality: ModalityModel = field(default_factory=ModalityModel)
    focus: FocusModel = field(default_factory=FocusModel)
    confidence: ConfidenceModel = field(default_factory=ConfidenceModel)
    errors: ErrorModel = field(default_factory=ErrorModel)
    challenge: ChallengeModel = field(default_factory=ChallengeModel)
    total_events: int = 0
    subjects_seen: set[str] = field(default_factory=set)

    def update(self, event: InteractionEvent) -> None:
        """The single write path. Every learning moment flows through here."""
        if event.learner_id != self.learner_id:
            raise ValueError(
                f"Event for {event.learner_id!r} sent to profile {self.learner_id!r}"
            )

        if event.correct is not None:
            self.memory.observe(event.item_key, event.correct, event.timestamp)
            self.modality.observe(event.modality, event.correct, event.engaged)
        elif event.engaged:
            # Passive exposure still teaches us about modality engagement.
            self.modality.observe(event.modality, True, event.engaged)

        self.focus.observe(event)
        self.confidence.observe(event.confidence, event.correct)
        self.challenge.observe(event.difficulty, event.correct, event.engaged)
        if event.correct is False:
            self.errors.observe(event.error_signature, event.subject, event.timestamp)

        self.subjects_seen.add(event.subject)
        self.total_events += 1

    def summary(self) -> dict:
        """Human-readable snapshot — what we believe about this learner."""
        return {
            "learner_id": self.learner_id,
            "events_observed": self.total_events,
            "subjects_seen": sorted(self.subjects_seen),
            "memory": {
                "base_half_life_hours": round(self.memory.base_half_life_hours, 1),
                "stability_gain": round(self.memory.stability_gain, 2),
                "items_tracked": len(self.memory.items),
            },
            "modality_ranking": [
                (m.value, round(score, 3)) for m, score in self.modality.ranked()
            ],
            "attention_span_minutes": round(self.focus.attention_span_minutes, 1),
            "calibration_bias": round(self.confidence.calibration_bias, 3),
            "target_difficulty": round(self.challenge.target_difficulty(), 2),
            "recurring_error_count": len(self.errors.records),
        }
