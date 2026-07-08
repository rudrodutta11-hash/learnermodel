"""Tests for knowledge state, the recommendation engine, and persistence."""
import random
import time

from learner_model import InteractionEvent, LearnerProfile, Modality
from learner_model.history import SessionHistory
from learner_model.knowledge import Concept, KnowledgeState
from learner_model.store import load_profile, save_profile
from recommendation import RecommendationEngine, SessionContext

HOUR = 3600.0
NOW = time.time()


def learn(profile, concept_id, correct=True, ts=NOW, subject="spanish", **kw):
    profile.update(InteractionEvent(
        learner_id=profile.learner_id, subject=subject, item_id=concept_id,
        modality=kw.pop("modality", Modality.FLASHCARD), correct=correct,
        difficulty=0.5, duration_seconds=30, timestamp=ts, session_id="s", **kw,
    ))


def spanish_knowledge():
    ks = KnowledgeState(subject="spanish")
    ks.add_concepts([
        Concept(id="greetings", subject="spanish", name="Greetings", difficulty=0.2),
        Concept(id="numbers", subject="spanish", name="Numbers", difficulty=0.3),
        Concept(id="past-tense", subject="spanish", name="Past tense",
                difficulty=0.6, prerequisites=("greetings",)),
    ])
    return ks


class TestKnowledgeState:
    def test_frontier_respects_prerequisites(self):
        p = LearnerProfile(learner_id="u1")
        ks = spanish_knowledge()
        frontier_ids = {c.id for c in ks.frontier(p.memory, NOW)}
        assert frontier_ids == {"greetings", "numbers"}  # past-tense gated

    def test_review_queue_surfaces_decayed_concepts(self):
        p = LearnerProfile(learner_id="u1")
        ks = spanish_knowledge()
        learn(p, "greetings", ts=NOW - 200 * HOUR)
        due = ks.review_queue(p.memory, NOW)
        assert [s.concept.id for s in due] == ["greetings"]
        assert due[0].review_due_ts < NOW


class TestEngine:
    def setup_method(self):
        self.engine = RecommendationEngine()

    def rec(self, profile, ks, minutes=10):
        profile.modality.rng = random.Random(42)  # deterministic Thompson sampling
        return self.engine.recommend(
            profile, ks, SessionContext(subject="spanish", available_minutes=minutes),
        )

    def test_new_learner_gets_new_material(self):
        rec = self.rec(LearnerProfile(learner_id="u1"), spanish_knowledge())
        assert rec.need == "new_material"
        assert rec.explanation

    def test_decayed_memory_triggers_review(self):
        p = LearnerProfile(learner_id="u1")
        ks = spanish_knowledge()
        learn(p, "greetings", ts=NOW - 300 * HOUR)
        learn(p, "numbers", ts=NOW - 300 * HOUR)
        rec = self.rec(p, ks)
        assert rec.need == "review"
        assert set(rec.concepts) <= {"greetings", "numbers"}

    def test_time_budget_respected(self):
        rec = self.rec(LearnerProfile(learner_id="u1"), spanish_knowledge(), minutes=2)
        assert rec.estimated_minutes <= 2

    def test_preferred_modality_wins_for_review(self):
        p = LearnerProfile(learner_id="u1")
        ks = spanish_knowledge()
        # Strongly train flashcards; make quizzes fail badly
        for i in range(40):
            learn(p, f"x{i}", modality=Modality.FLASHCARD, correct=True)
            learn(p, f"y{i}", modality=Modality.QUIZ, correct=False)
        learn(p, "greetings", ts=NOW - 300 * HOUR)
        rec = self.rec(p, ks)
        if rec.need == "review":
            assert rec.activity_type == Modality.FLASHCARD


class TestPersistence:
    def test_profile_roundtrip(self, tmp_path):
        p = LearnerProfile(learner_id="u1")
        learn(p, "greetings", correct=False, error_signature="oops")
        save_profile(p, tmp_path)
        loaded = load_profile("u1", tmp_path)
        assert loaded.total_events == 1
        assert loaded.memory.items.keys() == p.memory.items.keys()
        assert loaded.errors.records.keys() == p.errors.records.keys()

    def test_history_replay_rebuilds_profile(self, tmp_path):
        history = SessionHistory(tmp_path)
        p = LearnerProfile(learner_id="u1")
        for i in range(5):
            e = InteractionEvent(
                learner_id="u1", subject="spanish", item_id=f"w{i}",
                modality=Modality.STORY, correct=True, timestamp=NOW,
                session_id="s1", duration_seconds=30,
            )
            history.append(e)
            p.update(e)
        rebuilt = LearnerProfile(learner_id="u1")
        history.replay_into(rebuilt)
        assert rebuilt.total_events == p.total_events
        assert rebuilt.memory.items.keys() == p.memory.items.keys()
