"""Built-in experience types for the MVP.

Each experience asks the Teacher to generate content dynamically from the
recommendation (concepts, difficulty, goal) — there is no hard-coded
curriculum anywhere. Adding a new format means writing one small class
and calling `register_experience`.
"""
from __future__ import annotations

from dataclasses import dataclass

from learner_model.events import Modality
from learner_model.profile import LearnerProfile
from recommendation.engine import Recommendation

from .base import ExperiencePlan, Teacher, register_experience


def _context(rec: Recommendation, profile: LearnerProfile, goal: str,
             memory: dict | None) -> dict:
    return {
        "goal": goal,
        "concepts": list(rec.concepts),
        "difficulty": rec.difficulty,
        "minutes": rec.estimated_minutes,
        "need": rec.need,
        "learner": profile.summary(),
        "memory": memory,   # session history Kai can reference naturally
    }


@dataclass(frozen=True)
class _PromptedExperience:
    """Shared shape: one teacher instruction + one interaction style."""

    activity_type: Modality
    title: str
    interaction: str
    instruction: str

    def build(
        self,
        recommendation: Recommendation,
        profile: LearnerProfile,
        teacher: Teacher,
        goal: str,
        memory: dict | None = None,
    ) -> ExperiencePlan:
        content = teacher.generate(
            self.instruction, _context(recommendation, profile, goal, memory)
        )
        return ExperiencePlan(
            activity_type=self.activity_type,
            title=self.title,
            content=content,
            interaction=self.interaction,
            concepts=recommendation.concepts,
            difficulty=recommendation.difficulty,
            estimated_minutes=recommendation.estimated_minutes,
        )


# Instructions are stage directions to Kai — his persona and his notes on
# this student (including past sessions) are already in the system prompt.

register_experience(_PromptedExperience(
    activity_type=Modality.MINI_LESSON,
    title="The idea",
    interaction="read-then-answer",
    instruction=(
        "Teach today's mini lesson. Open with one line in your voice — if "
        "your notes give you a real thread from a past session, pick it up. "
        "Explain the idea the way you'd explain it across a table, give 2-3 "
        "examples drawn from their interests where you can, and end with "
        "exactly one check question that makes them produce, not recognize."
    ),
))

register_experience(_PromptedExperience(
    activity_type=Modality.FLASHCARD,
    title="Rapid fire",
    interaction="answer",
    instruction=(
        "Build today's flashcard deck. One line of framing from you first — "
        "why THESE cards today (a fading memory, a recurring mistake — say "
        "which, plainly). Then the cards, one per line, 'front | back', "
        "easiest to hardest, ending one notch past comfortable."
    ),
))

register_experience(_PromptedExperience(
    activity_type=Modality.QUIZ,
    title="Prove it",
    interaction="answer",
    instruction=(
        "Set today's quiz. One line of framing in your voice — if this "
        "targets a mistake they keep making, name it like a teacher would, "
        "not an app. Then numbered questions mixing recall and application, "
        "climbing in difficulty. No answers; they earn those."
    ),
))

register_experience(_PromptedExperience(
    activity_type=Modality.WRITING,
    title="In your own words",
    interaction="write",
    instruction=(
        "Set one short writing task that forces today's concepts into use. "
        "Brief the task the way you'd say it aloud, connect it to their "
        "actual life or interests if your notes allow, and show one worked "
        "example at the quality bar you expect — then get out of the way."
    ),
))

register_experience(_PromptedExperience(
    activity_type=Modality.CONVERSATION,
    title="Talk with Kai",
    interaction="chat",
    instruction=(
        "Open today's conversation. If you have history together, pick up "
        "the thread naturally; if not, introduce yourself the way you do. "
        "One warm beat, then one question that makes them use today's "
        "concepts to answer it."
    ),
))

register_experience(_PromptedExperience(
    activity_type=Modality.STORY,
    title="The story",
    interaction="read-then-answer",
    instruction=(
        "Tell a short story that smuggles today's concepts in so they're "
        "absorbed in context — set it somewhere their interests live if you "
        "can. Your dry humor is welcome here. End with two questions about "
        "the concepts as they appeared in the story, one easy, one stretch."
    ),
))
