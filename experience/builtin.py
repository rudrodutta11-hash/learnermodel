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


def _context(rec: Recommendation, profile: LearnerProfile, goal: str) -> dict:
    return {
        "goal": goal,
        "concepts": list(rec.concepts),
        "difficulty": rec.difficulty,
        "minutes": rec.estimated_minutes,
        "need": rec.need,
        "learner": profile.summary(),
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
    ) -> ExperiencePlan:
        content = teacher.generate(
            self.instruction, _context(recommendation, profile, goal)
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


register_experience(_PromptedExperience(
    activity_type=Modality.MINI_LESSON,
    title="Mini lesson",
    interaction="read-then-answer",
    instruction=(
        "Teach a focused mini lesson on the given concepts (or, if none are "
        "given, choose the single most useful next thing for the learner's "
        "goal). Explain briefly, give 2-3 vivid examples, and end with one "
        "check-for-understanding question."
    ),
))

register_experience(_PromptedExperience(
    activity_type=Modality.FLASHCARD,
    title="Flashcards",
    interaction="answer",
    instruction=(
        "Create a short flashcard deck for the given concepts at the given "
        "difficulty. One line per card, 'front | back'. Order from easiest "
        "to hardest."
    ),
))

register_experience(_PromptedExperience(
    activity_type=Modality.QUIZ,
    title="Quick quiz",
    interaction="answer",
    instruction=(
        "Write a short quiz targeting the given concepts, especially any "
        "recurring mistakes. Mix recall and application. Number the "
        "questions; do not reveal answers."
    ),
))

register_experience(_PromptedExperience(
    activity_type=Modality.WRITING,
    title="Writing exercise",
    interaction="write",
    instruction=(
        "Design one short writing exercise that forces the learner to use "
        "the given concepts correctly. State the task and a worked example "
        "of the expected quality."
    ),
))

register_experience(_PromptedExperience(
    activity_type=Modality.CONVERSATION,
    title="Conversation",
    interaction="chat",
    instruction=(
        "Open a conversation that naturally exercises the given concepts. "
        "Start with a warm, one-paragraph opener and a question that pulls "
        "the learner in. Stay in character as their personal teacher."
    ),
))

register_experience(_PromptedExperience(
    activity_type=Modality.STORY,
    title="Story",
    interaction="read-then-answer",
    instruction=(
        "Tell a short, engaging story that embeds the given concepts so the "
        "learner absorbs them in context. End with two questions about the "
        "concepts as they appeared in the story."
    ),
))
