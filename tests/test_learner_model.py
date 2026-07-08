"""Tests for the universal learner model."""
import time

from learner_model import InteractionEvent, LearnerProfile, Modality
from learner_model.memory import MemoryModel

HOUR = 3600.0
NOW = time.time()


def make_event(**overrides) -> InteractionEvent:
    defaults = dict(
        learner_id="u1", subject="spanish", item_id="hola",
        modality=Modality.FLASHCARD, correct=True, difficulty=0.5,
        duration_seconds=30, engaged=True, timestamp=NOW, session_id="s1",
    )
    defaults.update(overrides)
    return InteractionEvent(**defaults)


class TestMemory:
    def test_retrievability_decays_over_time(self):
        m = MemoryModel(base_half_life_hours=24.0)
        m.observe("spanish:hola", True, NOW)
        assert m.retrievability("spanish:hola", NOW) == 1.0
        assert m.retrievability("spanish:hola", NOW + 24 * HOUR) == 0.5

    def test_successful_recall_extends_half_life(self):
        m = MemoryModel()
        m.observe("spanish:hola", True, NOW)
        before = m.items["spanish:hola"].half_life_hours
        m.observe("spanish:hola", True, NOW + 20 * HOUR)
        assert m.items["spanish:hola"].half_life_hours > before

    def test_failure_shrinks_half_life(self):
        m = MemoryModel()
        m.observe("spanish:hola", True, NOW)
        before = m.items["spanish:hola"].half_life_hours
        m.observe("spanish:hola", False, NOW + HOUR)
        assert m.items["spanish:hola"].half_life_hours < before

    def test_fast_forgetter_gets_shorter_base_half_life(self):
        m = MemoryModel(base_half_life_hours=24.0)
        # Repeatedly failing recalls the model predicted would succeed
        for i in range(10):
            key = f"spanish:w{i}"
            m.observe(key, True, NOW)
            m.observe(key, False, NOW + HOUR)  # predicted R ~0.97, failed
        assert m.base_half_life_hours < 24.0

    def test_items_at_risk_sorted_weakest_first(self):
        m = MemoryModel(base_half_life_hours=24.0)
        m.observe("spanish:old", True, NOW - 72 * HOUR)
        m.observe("spanish:fresh", True, NOW)
        at_risk = m.items_at_risk(NOW, threshold=0.9)
        assert at_risk[0][0] == "spanish:old"


class TestProfile:
    def test_modality_learning(self):
        p = LearnerProfile(learner_id="u1")
        for i in range(20):
            p.update(make_event(item_id=f"s{i}", modality=Modality.STORY, correct=True))
            p.update(make_event(item_id=f"q{i}", modality=Modality.QUIZ, correct=False))
        assert p.modality.effectiveness(Modality.STORY) > p.modality.effectiveness(Modality.QUIZ)
        assert p.modality.best() == Modality.STORY

    def test_disengagement_counts_against_modality(self):
        p = LearnerProfile(learner_id="u1")
        for i in range(10):
            p.update(make_event(item_id=f"v{i}", modality=Modality.VIDEO,
                                correct=True, engaged=False))
        assert p.modality.effectiveness(Modality.VIDEO) < 0.5

    def test_confidence_calibration_detects_overconfidence(self):
        p = LearnerProfile(learner_id="u1")
        for i in range(10):
            p.update(make_event(item_id=f"w{i}", confidence=0.9, correct=False))
        assert p.confidence.is_overconfident

    def test_recurring_errors_tracked(self):
        p = LearnerProfile(learner_id="u1")
        for i in range(3):
            p.update(make_event(item_id=f"ser{i}", correct=False,
                                error_signature="ser-vs-estar"))
        recurring = p.errors.recurring(NOW, subject="spanish")
        assert recurring and recurring[0].signature == "ser-vs-estar"

    def test_profile_is_cross_subject(self):
        p = LearnerProfile(learner_id="u1")
        for i in range(15):
            p.update(make_event(item_id=f"s{i}", modality=Modality.STORY, correct=True))
        # New subject, same profile: modality preference carries over
        assert p.modality.best() == Modality.STORY
        p.update(make_event(subject="history", item_id="ww1", modality=Modality.STORY))
        assert p.subjects_seen == {"spanish", "history"}

    def test_rejects_wrong_learner(self):
        p = LearnerProfile(learner_id="u1")
        import pytest
        with pytest.raises(ValueError):
            p.update(make_event(learner_id="u2"))
