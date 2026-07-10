"""Conversation validation harness — runs Kai against the REAL model.

Purpose: measure where the real model (cheap mode / Haiku) diverges from the
mock-tuned behavior, BEFORE touching any prompt. It drives one full 5-minute
session arc (open -> middle -> close) with a scripted learner, saves the
timestamped, phase-labeled transcript, scores the real output against our
existing rules, and logs the token cost.

It does NOT change any of Kai's prompts. It imports and reuses the shipping
pieces verbatim:
  - app.persona.system_prompt      (Kai's actual system prompt)
  - app.conversation.OPENER_CUE    (the actual opener stage direction)
  - app.conversation._phase_note   (the actual per-phase directive)
  - recommendation.preview.BANNED_WORDS (via harness.rules)
The only thing it does differently from the app is call the Anthropic client
directly so it can read `response.usage` for cost — otherwise the request is
byte-for-byte what app.teacher.AnthropicTeacher.chat sends.

Usage:
  export ANTHROPIC_API_KEY=sk-...
  python -m harness.validate_conversation                 # cheap / Haiku
  python -m harness.validate_conversation --model claude-opus-4-8
  python -m harness.validate_conversation --self-test     # rules only, no API
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from app.conversation import OPENER_CUE, _phase_note
from app.persona import system_prompt
from app.teacher import MODELS_BY_MODE
from harness import rules

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "beginner_spanish.json"
DEFAULT_OUT = REPO_ROOT / "transcripts"

# Per-MTok pricing (USD) for cost estimation. Haiku 4.5: $1 in / $5 out.
PRICING = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-4-8": (5.0, 25.0),
}


def _phase_label(note: str) -> str:
    if note.startswith("Opening"):
        return "opening"
    if note.startswith("Mid-session"):
        return "mid"
    if note.startswith("Closing"):
        return "closing"
    return "overtime"  # "Time's up..."


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _call(client, model: str, context: dict, messages: list[dict]) -> tuple[str, dict]:
    """Replicates app.teacher.AnthropicTeacher.chat exactly, but returns usage
    so we can price the run. Same model, max_tokens, system, and messages."""
    resp = client.messages.create(
        model=model,
        max_tokens=1000,
        system=system_prompt(context),
        messages=messages,
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    usage = {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens}
    return text, usage


def run_session(fixture: dict, model: str) -> dict:
    """Drive the full arc against the real model. Returns the transcript."""
    import anthropic

    client = anthropic.Anthropic()
    minutes = fixture["minutes"]
    base_context = dict(fixture["context"])
    messages: list[dict] = []
    turns: list[dict] = []
    totals = {"in": 0, "out": 0}

    def record(role: str, text: str, phase: str, elapsed: float | None,
               usage: dict | None) -> None:
        if usage:
            totals["in"] += usage["in"]
            totals["out"] += usage["out"]
        turns.append({
            "role": role, "phase": phase, "elapsed_min": elapsed,
            "timestamp": _now_iso(), "text": text,
            "tokens": usage,
        })

    # --- Opener (opening phase) ---
    opener_ctx = dict(base_context)
    opener_ctx["minutes"] = minutes
    opener_msg = [{"role": "user", "content": OPENER_CUE.format(minutes=minutes)}]
    opener, usage = _call(client, model, opener_ctx, opener_msg)
    messages.append({"role": "assistant", "content": opener})
    record("kai", opener, "opening", 0.0, usage)

    # --- Learner turns, each with the REAL phase directive for its elapsed time ---
    for lt in fixture["learner_turns"]:
        elapsed = lt["elapsed_min"]
        note = _phase_note(elapsed, minutes)
        phase = _phase_label(note)
        record("learner", lt["text"], phase, elapsed, None)
        messages.append({"role": "user", "content": lt["text"]})

        turn_ctx = dict(base_context)
        turn_ctx["minutes"] = minutes
        turn_ctx["time_note"] = note
        reply, usage = _call(client, model, turn_ctx, messages)
        messages.append({"role": "assistant", "content": reply})
        record("kai", reply, phase, elapsed, usage)

    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    cost = totals["in"] / 1e6 * price_in + totals["out"] / 1e6 * price_out
    return {
        "meta": {
            "fixture": fixture["name"],
            "model": model,
            "minutes": minutes,
            "run_at": _now_iso(),
            "tokens": totals,
            "estimated_usd": round(cost, 5),
        },
        "turns": turns,
    }


def expectations_from(fixture: dict) -> dict:
    """Pull the fixture's ground truth for the rules that need it."""
    error_index = next(
        (i for i, lt in enumerate(fixture["learner_turns"]) if lt.get("seeded_error")),
        None,
    )
    echo = next(
        (lt.get("expected_echo") for lt in fixture["learner_turns"]
         if lt.get("seeded_error")),
        "",
    )
    return {
        "expected_echo": echo,
        "error_turn_index": error_index,
        "concepts": fixture["context"]["concepts"],
    }


def render_markdown(transcript: dict, results: list[rules.RuleResult]) -> str:
    m = transcript["meta"]
    out = [
        f"# Kai conversation validation — {m['fixture']}",
        f"- model: `{m['model']}`  ·  budget: {m['minutes']} min  ·  run: {m['run_at']}",
        f"- tokens: {m['tokens']['in']} in / {m['tokens']['out']} out  ·  "
        f"est. cost: ${m['estimated_usd']}",
        "",
        "## Rule report",
        "",
        "| rule | result | kind | detail |",
        "|---|---|---|---|",
    ]
    for r in results:
        badge = "PASS" if r.passed else "FAIL"
        kind = "heuristic" if r.heuristic else "exact"
        detail = r.detail.replace("|", "\\|")
        out.append(f"| {r.name} | {badge} | {kind} | {detail} |")
    passed = sum(r.passed for r in results)
    out += ["", f"**{passed}/{len(results)} rules passed.**", "",
            "## Transcript", ""]
    for t in transcript["turns"]:
        who = "KAI" if t["role"] == "kai" else "LEARNER"
        stamp = f"[{t['phase']}"
        if t["elapsed_min"] is not None:
            stamp += f" · {t['elapsed_min']:.1f}m"
        stamp += "]"
        out.append(f"**{who}** {stamp}")
        out.append("")
        out.append(t["text"])
        out.append("")
    return "\n".join(out)


def _self_test() -> int:
    """Exercise the rule logic against a synthetic transcript — no API, no
    tokens. Lets us verify the checker itself in CI."""
    transcript = {
        "meta": {"fixture": "synthetic"},
        "turns": [
            {"role": "kai", "phase": "opening", "elapsed_min": 0.0,
             "text": "Back again — good. Say hello to Carlos in Spanish. What do you tell him?"},
            {"role": "learner", "phase": "mid", "elapsed_min": 2.2, "text": "yo tiene hambre"},
            {"role": "kai", "phase": "mid", "elapsed_min": 2.2,
             "text": "Almost — yo tengo, not tiene. Ownership is yours. Say it again?"},
            {"role": "learner", "phase": "closing", "elapsed_min": 4.8, "text": "yo tengo hambre!"},
            {"role": "kai", "phase": "closing", "elapsed_min": 4.8,
             "text": "There it is — you fixed tener cleanly and fast. That's the win today."},
        ],
    }
    exp = {"expected_echo": "tengo", "error_turn_index": 0,
           "concepts": ["tener-present", "ordering-food"]}
    results = rules.check_all(transcript, exp)
    for r in results:
        print(f"  {'PASS' if r.passed else 'FAIL'}  {r.name}: {r.detail}")
    ok = all(r.passed for r in results)
    print(f"self-test {'OK' if ok else 'FAILED'} ({sum(r.passed for r in results)}/{len(results)})")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate Kai against the real model.")
    ap.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--model", default=None,
                    help="override model; default follows AI_MODE (cheap→Haiku)")
    ap.add_argument("--strict", action="store_true",
                    help="exit nonzero if any rule fails")
    ap.add_argument("--self-test", action="store_true",
                    help="run the rule checks against a synthetic transcript (no API)")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set. This harness validates the "
              "REAL model; it will not silently run the scripted mock.",
              file=sys.stderr)
        return 2
    mode = os.environ.get("AI_MODE", "cheap").lower()
    model = args.model or MODELS_BY_MODE.get(mode, MODELS_BY_MODE["cheap"])

    fixture = json.loads(args.fixture.read_text())
    print(f"Running '{fixture['name']}' against {model} …")
    transcript = run_session(fixture, model)
    results = rules.check_all(transcript, expectations_from(fixture))

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = args.out / f"{stamp}_{fixture['name']}"
    base.with_suffix(".json").write_text(json.dumps(
        {"transcript": transcript,
         "report": [r.__dict__ for r in results]}, indent=2))
    base.with_suffix(".md").write_text(render_markdown(transcript, results))

    m = transcript["meta"]
    print(f"\n=== {fixture['name']} @ {model} ===")
    for r in results:
        print(f"  {'PASS' if r.passed else 'FAIL'}  "
              f"{r.name:<22} {'(heuristic)' if r.heuristic else '(exact)   '}  {r.detail}")
    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} rules passed  ·  "
          f"{m['tokens']['in']}+{m['tokens']['out']} tokens  ·  "
          f"~${m['estimated_usd']}")
    print(f"transcript: {base.with_suffix('.md')}")

    return 1 if (args.strict and passed < len(results)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
