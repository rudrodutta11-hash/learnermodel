"""The universal event vocabulary.

Every subject — Spanish, History, Medicine — talks to the learner model
through exactly one channel: `InteractionEvent`. The learner model never
knows what a verb conjugation or a treaty date *is*; it only sees how the
learner behaved. This is what makes the model universal.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Modality(str, Enum):
    """Delivery mechanisms. These are interchangeable pipes, not products."""

    FLASHCARD = "flashcard"
    QUIZ = "quiz"
    MINI_LESSON = "mini_lesson"
    STORY = "story"
    CONVERSATION = "conversation"
    WRITING = "writing"
    VIDEO = "video"
    GAME = "game"
    CALL = "call"


@dataclass(frozen=True)
class InteractionEvent:
    """A single observed moment of learning.

    Subjects emit these; the learner model consumes them. Everything the
    model knows about a person is inferred from a stream of these events.
    """

    learner_id: str
    subject: str                      # e.g. "spanish", "history" — a namespace, not a schema
    item_id: str                      # subject-scoped knowledge atom (word, fact, concept)
    modality: Modality
    correct: Optional[bool] = None    # None for passive exposure (e.g. watching a video)
    confidence: Optional[float] = None  # learner's self-reported confidence in [0, 1]
    difficulty: float = 0.5           # how hard the activity was, in [0, 1]
    duration_seconds: float = 0.0
    engaged: bool = True              # did the learner stay with it, or bail/idle?
    error_signature: Optional[str] = None  # canonical tag for the *kind* of mistake made
    timestamp: float = field(default_factory=time.time)
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def item_key(self) -> str:
        """Globally unique key for the knowledge atom."""
        return f"{self.subject}:{self.item_id}"


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]
