"""Per-learner story state: how far into each character's arc they are,
and what they'll remember.

This is deliberately separate from the LearnerProfile. The profile is
universal (how you learn); story state is content-specific (your history
with Carlos). A learner who switches subjects keeps their profile but
starts a fresh cast — the same way KnowledgeState is subject-scoped.
"""
from __future__ import annotations

from dataclasses import dataclass, field

MAX_MEMORIES = 6  # keep the most recent beats Kai can reference


@dataclass
class CharacterThread:
    """The learner's ongoing relationship with one character."""

    character_id: str
    beat_index: int = 0            # how many beats have played out
    encounters: int = 0            # times the learner has met them
    last_seen_ts: float = 0.0
    memories: list[str] = field(default_factory=list)

    def remember(self, note: str) -> None:
        if note:
            self.memories.append(note)
            del self.memories[:-MAX_MEMORIES]


@dataclass
class StoryState:
    learner_id: str
    threads: dict[str, CharacterThread] = field(default_factory=dict)

    def thread(self, character_id: str) -> CharacterThread | None:
        return self.threads.get(character_id)

    def total_encounters(self) -> int:
        return sum(t.encounters for t in self.threads.values())
