"""The Story Engine — reusable narrative sequencing over any cast.

Two jobs, both content-agnostic:

  next_scene(state, subject, now)   read-only: pick who the learner meets
                                    tonight and stage the current beat of
                                    their story.
  advance(state, character_id, ...) mutating: after the session, move that
                                    character's thread one beat forward and
                                    record what the learner will remember.

The engine knows nothing about taxis or quinceañeras — only how to keep an
ensemble alive: introduce new faces until the cast is established, then
rotate through whoever's been waiting longest, deepening each arc. That's
what makes it drop into any subject with a different cast.

Selection is deterministic (no RNG) so a learner's story unfolds the same
way on replay and tests can pin it.
"""
from __future__ import annotations

from .cast import Character, cast_for, character_by_id
from .state import CharacterThread, StoryState


class StoryEngine:
    def next_scene(
        self, state: StoryState, subject: str, now: float
    ) -> tuple[Character, int, dict]:
        """Choose tonight's character and build the scene. Read-only."""
        character = self._select(state, subject)
        thread = state.thread(character.id)
        beat_index = thread.beat_index if thread else 0
        return character, beat_index, self._scene(character, beat_index, thread)

    def advance(
        self, state: StoryState, subject: str, character_id: str,
        beat_index: int, now: float, extra_memory: str = "",
    ) -> None:
        """Record that the learner just lived the given beat. Mutating."""
        character = character_by_id(subject, character_id)
        if character is None:
            return
        thread = state.threads.setdefault(
            character_id, CharacterThread(character_id=character_id)
        )
        thread.encounters += 1
        thread.last_seen_ts = now
        if beat_index < len(character.arc):
            thread.remember(character.arc[beat_index].memory)
        thread.remember(extra_memory)
        # Advance to the next unplayed beat (never past the end of the arc).
        thread.beat_index = min(len(character.arc), max(thread.beat_index, beat_index + 1))

    # -- selection ---------------------------------------------------------

    def _select(self, state: StoryState, subject: str) -> Character:
        cast = cast_for(subject)
        if not state.threads:
            return cast[0]  # first ever: the friendliest way in

        unmet = [c for c in cast if c.id not in state.threads]
        active = [
            c for c in cast
            if c.id in state.threads
            and state.threads[c.id].beat_index < len(c.arc)
        ]
        total = state.total_encounters()

        # Grow the ensemble: always reach two characters fast, then fold a
        # new face in every third session so the world keeps expanding.
        introduce = unmet and (len(state.threads) < 2 or total % 3 == 2)
        if introduce:
            return unmet[0]

        pool = active or cast  # arcs all complete? revisit for a catch-up
        index = {c.id: i for i, c in enumerate(cast)}
        return min(
            pool,
            key=lambda c: (
                state.threads[c.id].last_seen_ts if c.id in state.threads else 0.0,
                index[c.id],
            ),
        )

    # -- scene staging -----------------------------------------------------

    def _scene(
        self, character: Character, beat_index: int, thread: CharacterThread | None
    ) -> dict:
        encounters = thread.encounters if thread else 0
        memories = list(thread.memories) if thread else []

        if beat_index < len(character.arc):
            beat = character.arc[beat_index]
            situation, hook, reveal, beat_id = (
                beat.situation, beat.hook, beat.reveal, beat.id
            )
        else:
            situation = character.resting
            hook = ("Low stakes tonight — two people who know each other, "
                    "just catching up.")
            reveal = ""
            beat_id = f"{character.id}-resting"

        return {
            "character": {
                "id": character.id,
                "name": character.name,
                "role": character.role,
                "persona": character.persona,
            },
            "returning": encounters > 0,
            "encounters": encounters,
            "situation": situation,
            "hook": hook,
            "reveal": reveal,
            "memories": memories,
            "beat_id": beat_id,
        }
