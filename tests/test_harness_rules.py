"""The validation harness's rule logic — verified without spending tokens.

The harness runs Kai against the real model; these tests prove the rules
that score its output are themselves correct, on both good and bad
transcripts, so a PASS/FAIL in a real run can be trusted.
"""
from app.conversation import _phase_note
from harness import rules
from harness.validate_conversation import _phase_label, expectations_from, render_markdown


def kai(text, phase="mid", elapsed=2.0):
    return {"role": "kai", "phase": phase, "elapsed_min": elapsed, "text": text}


def learner(text, phase="mid", elapsed=2.0):
    return {"role": "learner", "phase": phase, "elapsed_min": elapsed, "text": text}


GOOD = {
    "turns": [
        kai("Back again — good. Tell Carlos why you're in Peru, in Spanish?",
            "opening", 0.0),
        learner("yo tiene hambre", "mid", 2.2),
        kai("Almost — yo tengo, not tiene. The having is yours. Try it again?",
            "mid", 2.2),
        learner("yo tengo hambre", "closing", 4.8),
        kai("There it is — you fixed tener cleanly today. Good work.",
            "closing", 4.8),
    ]
}
EXP = {"expected_echo": "tengo", "error_turn_index": 0,
       "concepts": ["tener-present", "ordering-food"]}


class TestRulesOnGoodTranscript:
    def test_all_pass(self):
        results = {r.name: r for r in rules.check_all(GOOD, EXP)}
        assert all(r.passed for r in results.values()), \
            {n: r.detail for n, r in results.items() if not r.passed}


class TestBannedLanguage:
    def test_catches_syllabus_word(self):
        t = {"turns": [kai("Great, let's start today's lesson on food.")]}
        r = rules.check_banned_language(t)
        assert not r.passed and "lesson" in r.detail

    def test_catches_chatbot_phrase(self):
        t = {"turns": [kai("Great question! Let me help with that.")]}
        r = rules.check_banned_language(t)
        assert not r.passed and "great question" in r.detail.lower()

    def test_clean_passes(self):
        t = {"turns": [kai("Say it back to me — what do you tell Carlos?")]}
        assert rules.check_banned_language(t).passed


class TestProducingEarly:
    def test_fails_without_a_question(self):
        t = {"turns": [kai("Welcome. I am your teacher and I will help you learn.",
                           "opening", 0.0)]}
        assert not rules.check_producing_early(t).passed

    def test_passes_when_opener_asks_for_output(self):
        t = {"turns": [kai("Good to see you. Order a coffee from Carlos — what do you say?",
                           "opening", 0.0)]}
        assert rules.check_producing_early(t).passed


class TestEchoCorrection:
    def test_fails_when_fix_not_echoed(self):
        bad = {"turns": [
            kai("Hi. What brings you to Peru?", "opening", 0.0),
            learner("yo tiene hambre", "mid", 2.2),
            kai("Interesting! Tell me more about that.", "mid", 2.2),
        ]}
        r = rules.check_echo_correction(bad, "tengo", 0)
        assert not r.passed and "tengo" in r.detail

    def test_passes_when_echoed(self):
        assert rules.check_echo_correction(GOOD, "tengo", 0).passed


class TestTurnLength:
    def test_flags_a_long_turn(self):
        long = "One. Two. Three. Four. Five sentences here."
        r = rules.check_turn_length({"turns": [kai(long)]})
        assert not r.passed and "5 sentences" in r.detail

    def test_three_is_fine(self):
        ok = "First point. Second point. Now you try?"
        assert rules.check_turn_length({"turns": [kai(ok)]}).passed


class TestEndingSpecific:
    def test_generic_close_fails(self):
        t = {"turns": [kai("Great job today, see you next time!", "closing", 4.8)]}
        assert not rules.check_ending_specific(t, ["tener-present"]).passed

    def test_specific_close_passes(self):
        t = {"turns": [kai("You handled tener cleanly today — better than last time.",
                           "closing", 4.8)]}
        assert rules.check_ending_specific(t, ["tener-present"]).passed


class TestHarnessPlumbing:
    def test_phase_label_tracks_the_real_phase_note(self):
        # The label is derived from the REAL _phase_note across the arc.
        assert _phase_label(_phase_note(0.5, 5)) == "opening"
        assert _phase_label(_phase_note(2.5, 5)) == "mid"
        assert _phase_label(_phase_note(4.2, 5)) == "closing"
        assert _phase_label(_phase_note(9.0, 5)) == "overtime"

    def test_expectations_pulled_from_fixture(self):
        import json
        from pathlib import Path
        fx = json.loads((Path("harness/fixtures/beginner_spanish.json")).read_text())
        exp = expectations_from(fx)
        assert exp["expected_echo"] == "tengo"
        assert exp["error_turn_index"] == 1          # 2nd learner turn (0-indexed)
        assert "tener-present" in exp["concepts"]

    def test_markdown_report_renders(self):
        results = rules.check_all(GOOD, EXP)
        md = render_markdown({"meta": {"fixture": "x", "model": "m", "minutes": 5,
                                       "run_at": "now",
                                       "tokens": {"in": 10, "out": 20},
                                       "estimated_usd": 0.001}, **GOOD}, results)
        assert "Rule report" in md and "## Transcript" in md and "KAI" in md
