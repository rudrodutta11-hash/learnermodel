"""Episode previews — recommendations pitched like the next chapter.

The engine decides WHAT happens next (concepts, difficulty, need); this
module decides how it's PITCHED. Nobody ever hit play because the next
episode was called "Lesson 4: Review Exercises". They hit play to find
out what happens next.

Rules:
- Never use educational language. No lesson, exercise, module, practice,
  drill, quiz, review, study. (Enforced by tests against BANNED_WORDS.)
- Every preview is a story beat: the review queue is "the ones getting
  away", a recurring mistake is the season's recurring villain, new
  material is unexplored territory.
- Deterministic: previews are composed, not LLM-generated, so the
  dashboard costs nothing per click and tests can pin the copy. Variants
  rotate by a stable hash so the same learner sees fresh copy as their
  story advances, and the same request always renders the same card.
"""
from __future__ import annotations

import hashlib
from typing import Any

# The guard-rail. Tests scan every preview and engine string against this.
BANNED_WORDS = (
    "lesson", "exercise", "module", "practice", "practise", "drill",
    "quiz", "review", "study", "curriculum", "homework", "assignment",
    "worksheet", "syllabus", "flashcard",
)


def episode_preview(
    *,
    need: str,
    concepts: list[str],
    minutes: float,
    goal: str = "",
    memory: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Compose the episode card for one recommendation.

    Returns {"kicker", "title", "teaser"} — the whole learner-facing pitch.
    """
    memory = memory or {}
    chapter = int(memory.get("sessions_together") or 0) + 1
    names = [_humanize(c) for c in concepts]
    variant = _variant(need, names, chapter)

    if need == "review":
        title, teaser = _fading(names, variant)
    elif need == "fix_errors":
        title, teaser = _villain(names, variant, memory)
    else:
        title, teaser = _frontier(names, variant, minutes, goal)

    kicker = (f"Chapter {chapter} · where your story starts" if chapter == 1
              else f"Chapter {chapter} · {minutes:.0f} min")
    return {"kicker": kicker, "title": title, "teaser": teaser}


# -- the three story beats ----------------------------------------------------

def _fading(names: list[str], variant: int) -> tuple[str, str]:
    """The review queue, told as a heist: memories slipping out the back."""
    items = _list_phrase(names) or "a few things you'd sworn were yours"
    first = names[0] if names else "something you won last week"
    options = [
        ("The ones getting away",
         f"Somewhere in the back of your mind, {items} are quietly packing "
         f"their bags. You can still catch them at the door."),
        ("Still yours?",
         f"You won {items} fair and square. Memory has other plans. "
         f"Come collect what belongs to you."),
        ("The vanishing act",
         f"A few days ago, {first} was solid ground. Tonight it's "
         f"flickering. Catch it mid-escape and it's yours for good."),
    ]
    return options[variant % len(options)]


def _villain(names: list[str], variant: int, memory: dict) -> tuple[str, str]:
    """The recurring mistake as the season's recurring villain."""
    villain = names[0] if names else "an old nemesis"
    count = next(
        (c for sig, c in memory.get("recurring_mistakes") or []
         if _humanize(sig) == villain),
        None,
    )
    score = (f"It has taken you down {count} times." if count
             else "It keeps finding its way back.")
    options = [
        ("The rematch",
         f"{score} You've seen every move {villain} has. "
         f"This time it ends differently."),
        ("Old enemy, new you",
         f"{villain.capitalize()} again. {score} Kai thinks you're finally "
         f"ready for it. One way to find out."),
        (f"{villain.capitalize()} returns",
         f"Every good story has a villain that keeps coming back. Yours "
         f"picked tonight to show up. Bad timing — for it."),
    ]
    return options[variant % len(options)]


def _frontier(names: list[str], variant: int, minutes: float,
              goal: str) -> tuple[str, str]:
    """New material as territory nobody's spoiled yet."""
    if not names:
        return (
            "Kai's pick",
            f"He won't say what it is. He will say you're ready for it, "
            f"and that it takes about {minutes:.0f} minutes. "
            f"That's the whole trailer.",
        )
    first = names[0]
    toward = f" It's the shortest path to {goal.lower()}." if goal else ""
    options = [
        ("Uncharted",
         f"Everything behind you is conquered ground. Ahead: {first}. "
         f"No spoilers — come see what's in there.{toward}"),
        ("The next door",
         f"You've earned the key to {first}. Kai's already on the other "
         f"side, waiting. He looks pleased about something."),
        ("First contact",
         f"You and {first} have never met. That changes in the next "
         f"{minutes:.0f} minutes."),
    ]
    return options[variant % len(options)]


# -- helpers -------------------------------------------------------------------

def _humanize(concept_id: str) -> str:
    return str(concept_id).replace("-", " ").replace("_", " ").strip()


def _list_phrase(names: list[str], limit: int = 3) -> str:
    shown = names[:limit]
    if not shown:
        return ""
    if len(shown) == 1:
        return shown[0]
    return ", ".join(shown[:-1]) + " and " + shown[-1]


def _variant(need: str, names: list[str], chapter: int) -> int:
    """Stable per-request, fresh across chapters."""
    seed = f"{need}:{','.join(names)}:{chapter}".encode()
    return int(hashlib.md5(seed).hexdigest(), 16)
