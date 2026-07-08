"""The AI Teacher — Kai, the main character of this product.

Kai's personality lives in app/persona.py; this module is the machinery
that speaks with it. EVERY AI interaction — lesson generation, live
conversation, session assessment — routes through the persona, so Kai is
one consistent person whether he's writing flashcards or chatting.

Voice-readiness: the Teacher separates WHAT is said (this class) from HOW
it is delivered (the transport). Today the transport is text over HTTP;
tomorrow it can be TTS or a phone call. `generate()` and `chat()` return
plain content with no channel assumptions, so plugging in voice means
adding a transport, not rewriting experiences.

Backends, selected by the AI_MODE env var:
  - mock (default): ScriptedTeacher — deterministic, offline, zero cost.
    Use this for all day-to-day development so you never burn credits.
    Even the mock speaks in Kai's voice and uses his session memory.
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

from .persona import system_prompt

MODELS_BY_MODE = {
    "cheap": "claude-haiku-4-5",
    "premium": "claude-opus-4-8",
}


class AnthropicTeacher:
    """Claude-backed Kai."""

    def __init__(self, model: str) -> None:
        import anthropic

        self._client = anthropic.Anthropic()
        self._model = model

    def generate(self, instruction: str, context: dict[str, Any]) -> str:
        """One-shot content in Kai's voice (lessons, decks, quizzes...)."""
        response = self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=system_prompt(context),
            messages=[{"role": "user", "content": instruction}],
        )
        return "".join(b.text for b in response.content if b.type == "text")

    def chat(self, messages: list[dict[str, str]], context: dict[str, Any]) -> str:
        """Multi-turn conversation — the surface a voice transport will drive."""
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1000,
            system=system_prompt(context),
            messages=messages,
        )
        return "".join(b.text for b in response.content if b.type == "text")

    def assess(self, transcript: list[dict[str, str]], context: dict[str, Any]) -> dict:
        """Grade a finished conversation into structured session results.
        The grading is clinical JSON; only `notes` is Kai speaking."""
        planned = context.get("concepts") or []
        prompt = (
            "The session just ended. Step out of the conversation and write "
            "your private teaching notes as ONLY a JSON object, no other text:\n"
            '{"concepts": ["<id of each concept actually practiced>"], '
            '"mistakes": ["<short-kebab-case signature of each recurring or '
            'notable mistake>"], "confidence": <your 0..1 read of the '
            "student's confidence>, "
            '"notes": "<one sentence TO the student, in your own voice — '
            'specific, no empty praise>", '
            '"journal": "<1-2 sentences in your private teaching notebook '
            "about HOW this student learns — a confidence pattern, their "
            "pacing, a misconception and its shape, an analogy or approach "
            "that landed (or flopped), a focus habit, a breakthrough, what "
            "sparked their curiosity. Strictly pedagogical: no personal-life "
            "details, no small talk. Write what a great teacher would want "
            'their future self to know before the next lesson.>"}\n'
            f"Planned concepts were: {json.dumps(planned)} — reuse those ids "
            "where they apply; add kebab-case ids for anything else practiced.\n\n"
            "Transcript:\n"
            + "\n".join(f"{m['role']}: {m['content']}" for m in transcript)
        )
        raw = self.generate(prompt, context)
        parsed = _extract_json(raw)
        if parsed is not None:
            return _clean_assessment(parsed, planned)
        return _fallback_assessment(planned)


class ScriptedTeacher:
    """Deterministic offline Kai: keeps the product runnable and testable
    with zero API cost. Same interface, same personality — canned lines
    composed from the same memory the real Kai reads, so even the mock
    references previous sessions when they exist."""

    def generate(self, instruction: str, context: dict[str, Any]) -> str:
        concepts = [str(c) for c in context.get("concepts") or []]
        goal = context.get("goal", "your goal")
        minutes = context.get("minutes", "a few")
        focus = ", ".join(concepts) if concepts else "the next most useful thing"
        opener = _memory_line(context)
        return (
            f"{opener}We've got {minutes} minutes — enough for {focus}. "
            f"It's the highest-value step toward {goal} right now, so let's "
            f"not waste it.\n\n({instruction})"
        )

    def chat(self, messages: list[dict[str, str]], context: dict[str, Any]) -> str:
        last = messages[-1]["content"] if messages else ""
        if last.startswith("("):  # stage direction → session opener
            scene = context.get("story_scene")
            if scene:
                return _scene_opener(scene, context)
            concepts = [str(c) for c in context.get("concepts") or []]
            focus = concepts[0] if concepts else "something new"
            return (
                f"{_memory_line(context)}Today I want to hear you use "
                f"{focus} — not recite it, use it. So: tell me about your "
                f"day, and work it in."
            )
        return (
            f"Good — and notice what you just did there. Push it one step: "
            f"say that again, but change one thing about it. "
            f"(You said: {last[:60]})"
        )

    def assess(self, transcript: list[dict[str, str]], context: dict[str, Any]) -> dict:
        return _fallback_assessment(context.get("concepts") or [])


def _scene_opener(scene: dict[str, Any], context: dict[str, Any]) -> str:
    """Mock Kai staging a story scene — enough to feel the narrative in
    development without touching the API. The real Kai does this richly."""
    char = scene.get("character") or {}
    name = char.get("name", "someone")
    role = char.get("role", "")
    greet = "I'm Kai. " if not (context.get("memory") or {}).get("sessions_together") else ""
    if scene.get("returning") and scene.get("memories"):
        return (f"{greet}{scene['situation']} You two have history — "
                f"{scene['memories'][-1]} How do you pick things back up "
                f"with {name}? In Spanish.")
    return (f"{greet}{scene['situation']} This is {name}, the {role}. "
            f"Go on — say something to them.")


def _memory_line(context: dict[str, Any]) -> str:
    """Kai's opening beat: grounded in real history, or an honest hello."""
    memory = context.get("memory") or {}
    if not memory.get("sessions_together"):
        return "I'm Kai — I'll be your teacher from here on. "
    last = memory.get("last_session") or {}
    if last.get("mistakes"):
        return (f"Back again — good. Last time {last['mistakes'][0]} "
                f"gave you trouble, and I haven't forgotten. ")
    if last.get("concepts"):
        return (f"Good to see you. Let's find out how much of "
                f"{last['concepts'][0]} survived since last time. ")
    return "Good to see you back. "


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of model output, tolerating prose."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _clean_assessment(parsed: dict, planned: list) -> dict:
    concepts = [str(c) for c in parsed.get("concepts") or planned] or ["conversation-practice"]
    mistakes = [str(m) for m in parsed.get("mistakes") or []]
    try:
        confidence = min(1.0, max(0.0, float(parsed.get("confidence", 0.6))))
    except (TypeError, ValueError):
        confidence = 0.6
    return {
        "concepts": concepts,
        "mistakes": mistakes,
        "confidence": confidence,
        "notes": str(parsed.get("notes", "")),
        "journal": str(parsed.get("journal", "")).strip(),
    }


def _fallback_assessment(planned: list) -> dict:
    focus = str(planned[0]) if planned else "new material"
    return {
        "concepts": [str(c) for c in planned] or ["conversation-practice"],
        "mistakes": [],
        "confidence": 0.6,
        "notes": "Solid session — you showed up and did the work. "
                 "Same time tomorrow and it starts compounding.",
        # Mock journal entry: same pedagogical shape as the real one.
        "journal": f"Worked {focus} in conversation; held steady when pushed "
                   f"to produce rather than recognize. Next time, open one "
                   f"notch harder and watch the pacing.",
    }


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
