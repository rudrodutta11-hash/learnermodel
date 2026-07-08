"""The AI Teacher — the signature feature.

One consistent teacher per product, defined by a single persona prompt.
Every experience type speaks through this object, so the learner always
hears the same voice whether they're doing flashcards or a conversation.

Voice-readiness: the Teacher separates WHAT is said (this class) from HOW
it is delivered (the transport). Today the transport is text over HTTP;
tomorrow it can be TTS or a phone call. `generate()` and `chat()` return
plain content with no channel assumptions, so plugging in voice means
adding a transport, not rewriting experiences.

Backends, selected by the AI_MODE env var:
  - mock (default): ScriptedTeacher — deterministic, offline, zero cost.
    Use this for all day-to-day development so you never burn credits.
  - cheap: AnthropicTeacher on Haiku — cheapest real model, for testing
    actual AI output without premium spend.
  - premium: AnthropicTeacher on Opus — production-quality generation.

AI_MODE defaults to "mock" specifically so running the app or tests never
costs anything unless you opt in.
"""
from __future__ import annotations

import json
import os
from typing import Any

PERSONA = """\
You are Kai, a personal AI teacher. You are the SAME teacher every session:
warm, direct, endlessly curious about how your student learns best.

Principles:
- You teach one specific human, not an audience. Use what you know about
  how they learn (provided as learner context) to shape every response.
- Never overwhelm: respect the time budget you're given.
- Adjust difficulty to keep success likely but not certain.
- Celebrate genuine progress specifically; never with empty praise.
- When the student errs, treat it as information, not failure.

You will receive an instruction describing the learning experience to
create, plus JSON context about the learner and their goal. Produce only
the experience content — no meta-commentary about being an AI.
"""

MODELS_BY_MODE = {
    "cheap": "claude-haiku-4-5",
    "premium": "claude-opus-4-8",
}


class AnthropicTeacher:
    """Claude-backed teacher."""

    def __init__(self, model: str) -> None:
        import anthropic

        self._client = anthropic.Anthropic()
        self._model = model

    def generate(self, instruction: str, context: dict[str, Any]) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=PERSONA,
            messages=[{
                "role": "user",
                "content": (
                    f"Instruction: {instruction}\n\n"
                    f"Context:\n{json.dumps(context, indent=2, default=str)}"
                ),
            }],
        )
        return "".join(b.text for b in response.content if b.type == "text")

    def chat(self, messages: list[dict[str, str]], context: dict[str, Any]) -> str:
        """Multi-turn conversation — the surface a voice transport will drive."""
        system = (
            PERSONA
            + "\nLearner context:\n"
            + json.dumps(context, indent=2, default=str)
        )
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1000,
            system=system,
            messages=messages,
        )
        return "".join(b.text for b in response.content if b.type == "text")


class ScriptedTeacher:
    """Deterministic fallback: keeps the product runnable and testable
    offline. Same interface, canned but context-aware content."""

    def generate(self, instruction: str, context: dict[str, Any]) -> str:
        concepts = context.get("concepts") or ["something new for your goal"]
        goal = context.get("goal", "your goal")
        return (
            f"[Kai] Let's spend {context.get('minutes', 'a few')} minutes on "
            f"{', '.join(str(c) for c in concepts)} — chosen because it's the "
            f"highest-impact step toward {goal} right now.\n\n"
            f"({instruction})"
        )

    def chat(self, messages: list[dict[str, str]], context: dict[str, Any]) -> str:
        last = messages[-1]["content"] if messages else ""
        return f"[Kai] Interesting — tell me more about: {last[:80]}"


def make_teacher():
    """Factory, controlled by AI_MODE:

      mock    (default) -> ScriptedTeacher, zero cost, no network
      cheap              -> AnthropicTeacher on Haiku
      premium            -> AnthropicTeacher on Opus

    Falls back to ScriptedTeacher if AI_MODE requests a real model but
    ANTHROPIC_API_KEY is missing or the client fails to initialize —
    the app should never hard-fail just because credits ran out.
    """
    mode = os.environ.get("AI_MODE", "mock").lower()
    if mode not in ("mock", "cheap", "premium"):
        raise ValueError(f"Invalid AI_MODE {mode!r}; expected mock, cheap, or premium")

    if mode == "mock":
        return ScriptedTeacher()

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return AnthropicTeacher(model=MODELS_BY_MODE[mode])
        except Exception:
            pass
    return ScriptedTeacher()
