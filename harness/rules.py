"""Rule checks for a Kai conversation transcript.

Pure functions over a transcript dict — no network, no tokens — so the
checking logic is itself unit-testable against synthetic transcripts. The
rules encode the behavior Kai's prompt asks for; the harness runs the REAL
model and then scores its output here.

Each check returns a RuleResult. Some rules are exact (banned language,
echo-correction against a known fix); others are heuristic (sentence
counting, "named something specific") and are flagged as such — the raw
transcript is always saved for manual review where a heuristic is soft.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from recommendation.preview import BANNED_WORDS

# Chatbot-tells the persona explicitly forbids ("How you talk" → Never).
CHATBOT_PHRASES = (
    "great question", "certainly!", "i hope this helps", "let's dive in",
    "it's important to note", "as an ai", "as a language model",
    "i'd be happy to", "feel free to",
)

# Affirmation cues used to detect a genuine closing compliment.
AFFIRMATION_CUES = (
    "nice", "good", "well done", "better", "faster", "cleaner", "sharp",
    "you nailed", "you handled", "you got", "proud", "that's the", "there it is",
)


@dataclass
class RuleResult:
    name: str
    passed: bool
    detail: str
    heuristic: bool = False


def _kai_turns(transcript: dict) -> list[dict]:
    return [t for t in transcript["turns"] if t["role"] == "kai"]


def _sentences(text: str) -> list[str]:
    """Rough sentence split: collapse ellipses, then split on . ! ? runs.
    Heuristic — good enough to flag a turn that has clearly run long."""
    t = re.sub(r"\.\.\.+|…", "…", text.strip())
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", t) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def check_banned_language(transcript: dict) -> RuleResult:
    """No syllabus vocabulary (BANNED_WORDS) and no chatbot tells."""
    hits: list[str] = []
    for turn in _kai_turns(transcript):
        low = turn["text"].lower()
        for w in BANNED_WORDS:
            if re.search(rf"\b{re.escape(w)}\b", low):
                hits.append(f"[{turn['phase']}] banned word {w!r}")
        for p in CHATBOT_PHRASES:
            if p in low:
                hits.append(f"[{turn['phase']}] chatbot phrase {p!r}")
    return RuleResult(
        "banned_language", not hits,
        "clean — no syllabus words or chatbot tells" if not hits
        else "; ".join(hits),
    )


def check_producing_early(transcript: dict) -> RuleResult:
    """The opener must pull the learner into producing, not listening —
    the persona's 'have them producing within your next exchange'. Proxy:
    the opener ends on a question that asks the learner to say something."""
    kai = _kai_turns(transcript)
    if not kai:
        return RuleResult("producing_early", False, "no Kai turns", heuristic=True)
    opener = kai[0]["text"]
    ends_on_question = opener.rstrip().endswith("?") or "?" in opener.split("\n")[-1]
    has_q = "?" in opener
    passed = has_q and ends_on_question
    return RuleResult(
        "producing_early", passed,
        "opener ends on a question that asks for output" if passed
        else f"opener does not clearly prompt production: …{opener[-80:]!r}",
        heuristic=True,
    )


def check_echo_correction(transcript: dict, expected_echo: str,
                          error_turn_index: int) -> RuleResult:
    """When the learner makes the seeded error, Kai's very next turn should
    echo the corrected form ('echo their sentence back fixed'). Exact-ish:
    the corrected token must appear in that reply."""
    turns = transcript["turns"]
    # Find the learner turn at the seeded error, then the next Kai turn.
    learner_seen = -1
    reply = None
    for t in turns:
        if t["role"] == "learner":
            learner_seen += 1
            armed = learner_seen == error_turn_index
        elif t["role"] == "kai" and learner_seen == error_turn_index:
            reply = t
            break
    if reply is None:
        return RuleResult("echo_correction", False,
                          "no Kai reply found after the seeded error")
    present = re.search(rf"\b{re.escape(expected_echo)}\b", reply["text"], re.I)
    return RuleResult(
        "echo_correction", bool(present),
        f"echoed the fix {expected_echo!r}" if present
        else f"did NOT echo {expected_echo!r}; reply was: {reply['text'][:120]!r}",
    )


def check_turn_length(transcript: dict, max_sentences: int = 3) -> RuleResult:
    """One-to-three sentences a turn. Heuristic sentence count; flags any
    conversational turn that clearly runs long."""
    long_turns = []
    for turn in _kai_turns(transcript):
        n = len(_sentences(turn["text"]))
        if n > max_sentences:
            long_turns.append(f"[{turn['phase']}] {n} sentences")
    return RuleResult(
        "turn_length_1_to_3", not long_turns,
        f"all turns ≤ {max_sentences} sentences" if not long_turns
        else "over-long: " + "; ".join(long_turns),
        heuristic=True,
    )


def check_ending_specific(transcript: dict, concepts: list[str]) -> RuleResult:
    """The close names one specific thing the learner did well — an
    affirmation cue plus a concrete reference to today's material. Heuristic."""
    closing = [t for t in _kai_turns(transcript)
               if t["phase"] in ("closing", "overtime")]
    if not closing:
        return RuleResult("ending_names_specific", False,
                          "no closing/overtime Kai turn to judge", heuristic=True)
    final = closing[-1]["text"].lower()
    has_affirm = any(cue in final for cue in AFFIRMATION_CUES)
    concept_words = set()
    for c in concepts:
        concept_words.update(re.split(r"[-_\s]", c.lower()))
    references = any(w and w in final for w in concept_words)
    passed = has_affirm and references
    return RuleResult(
        "ending_names_specific", passed,
        "close affirms + references today's material" if passed
        else f"weak close (affirm={has_affirm}, references_concept={references}): "
             f"{closing[-1]['text'][:120]!r}",
        heuristic=True,
    )


def check_all(transcript: dict, expectations: dict) -> list[RuleResult]:
    """Run every rule. `expectations` carries fixture-specific truth:
    expected_echo, error_turn_index, concepts."""
    return [
        check_banned_language(transcript),
        check_producing_early(transcript),
        check_echo_correction(transcript, expectations["expected_echo"],
                              expectations["error_turn_index"]),
        check_turn_length(transcript),
        check_ending_specific(transcript, expectations["concepts"]),
    ]
