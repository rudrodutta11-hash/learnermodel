"""Batch vs realtime analysis: mode mapping, one-call batch, cost ledger."""
import importlib
import random

import pytest
from fastapi.testclient import TestClient

from app.analysis import (
    AnalysisMode,
    analysis_mode_for,
    compact_results,
    cost_prefers_batch,
)
from learner_model import LearnerProfile
from learner_model.knowledge import Concept, KnowledgeState
from recommendation import RecommendationEngine, SessionContext


class TestAnalysisMode:
    def test_live_activities_are_realtime(self):
        assert analysis_mode_for("conversation") is AnalysisMode.REALTIME
        assert analysis_mode_for("writing") is AnalysisMode.REALTIME

    def test_structured_activities_are_batch(self):
        for a in ("flashcard", "quiz", "mini_lesson", "story"):
            assert analysis_mode_for(a) is AnalysisMode.BATCH

    def test_unknown_defaults_to_cheap_batch(self):
        assert analysis_mode_for("nonsense") is AnalysisMode.BATCH

    def test_cost_policy_follows_ai_mode(self, monkeypatch):
        monkeypatch.setenv("AI_MODE", "mock")
        assert cost_prefers_batch() is True
        monkeypatch.setenv("AI_MODE", "cheap")
        assert cost_prefers_batch() is True
        monkeypatch.setenv("AI_MODE", "premium")
        assert cost_prefers_batch() is False


class TestCompactResults:
    def test_sends_aggregates_and_only_missed_items_capped(self):
        missed = [{"prompt": f"w{i}"} for i in range(20)]
        r = compact_results(
            activity_type="flashcard", concepts=["a", "b"], mistakes=["b"],
            attempted=30, correct=25, missed_items=missed, max_items=8,
        )
        assert r["counts"] == {"attempted": 30, "correct": 25}
        assert len(r["missed_items"]) == 8              # capped — never full history
        assert "a" in r["concepts"]

    def test_counts_default_from_concepts_and_mistakes(self):
        r = compact_results(activity_type="quiz", concepts=["a", "b", "c"],
                            mistakes=["c"])
        assert r["counts"] == {"attempted": 3, "correct": 2}


class TestEngineBatchPreference:
    def _engine_pick(self, prefer_batch):
        profile = LearnerProfile(learner_id="u")
        profile.modality.rng = random.Random(3)
        ks = KnowledgeState(subject="s")
        ks.add_concepts([Concept(id="g", subject="s", name="G")])
        return RecommendationEngine().recommend(profile, ks, SessionContext(
            subject="s", available_minutes=10, prefer_batch=prefer_batch,
        ))

    def test_prefer_batch_penalizes_realtime_scores(self):
        from recommendation.engine import REALTIME_ACTIVITIES, BATCH_PREFERENCE_FACTOR
        # A realtime activity's score is discounted under prefer_batch; a
        # batch one is untouched. Compare the same activity both ways.
        from learner_model.events import Modality
        eng = RecommendationEngine()
        ctx_cheap = SessionContext(subject="s", available_minutes=10, prefer_batch=True)
        ctx_prem = SessionContext(subject="s", available_minutes=10, prefer_batch=False)
        assert eng._cost_factor(Modality.CONVERSATION, ctx_cheap) == BATCH_PREFERENCE_FACTOR
        assert eng._cost_factor(Modality.CONVERSATION, ctx_prem) == 1.0
        assert eng._cost_factor(Modality.FLASHCARD, ctx_cheap) == 1.0  # batch untouched


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEARNER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_MODE", "mock")
    from app import analysis, conversation, db, main, state
    for module in (analysis, db, state, conversation, main):
        importlib.reload(module)
    with TestClient(main.app) as c:
        yield c


def _auth(client):
    r = client.post("/api/register", json={"email": "a@b.co", "password": "hunter2plus"})
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    client.post("/api/onboarding", headers=headers, json={
        "goal": "Learn Spanish", "level": "beginner",
        "interests": ["travel"], "motivation": "trip",
    })
    return headers


class TestBatchPath:
    def test_batch_activity_costs_exactly_one_call(self, client):
        headers = _auth(client)
        r = client.get("/api/recommendation", headers=headers, params={"minutes": 5})
        sid = r.json()["session_id"]
        client.post("/api/session-summary", headers=headers, json={
            "activity_type": "flashcard", "concepts": ["a", "b", "c"],
            "mistakes": ["b"], "confidence": 0.7, "duration_seconds": 90,
            "difficulty": 0.4, "session_id": sid,
            "attempted": 3, "correct": 2,
        })
        report = client.get("/api/costs", headers=headers).json()
        flash = [r for r in report["by_activity"]
                 if r["activity_type"] == "flashcard"][0]
        assert flash["analysis_mode"] == "batch_analysis"
        assert flash["calls"] == 1                 # ONE call, not one-per-card
        assert report["batch_calls"] == 1

    def test_batch_activity_earns_a_journal_entry(self, client):
        """The single batch call gives a structured activity what only
        conversations used to get: a teaching-journal entry."""
        from app import state
        headers = _auth(client)
        from app import auth
        uid = auth.user_id_for_token(headers["Authorization"].removeprefix("Bearer "))
        r = client.get("/api/recommendation", headers=headers, params={"minutes": 5})
        client.post("/api/session-summary", headers=headers, json={
            "activity_type": "quiz", "concepts": ["a"], "mistakes": [],
            "confidence": 0.8, "duration_seconds": 60, "difficulty": 0.5,
            "session_id": r.json()["session_id"],
        })
        assert state.read_journal(uid)             # a note was written

    def test_disengaged_batch_makes_no_ai_call(self, client):
        headers = _auth(client)
        client.post("/api/session-summary", headers=headers, json={
            "activity_type": "flashcard", "concepts": [], "mistakes": [],
            "confidence": None, "duration_seconds": 5, "difficulty": 0.4,
            "engaged": False,
        })
        report = client.get("/api/costs", headers=headers).json()
        assert report["total_ai_calls"] == 0       # nothing happened, nothing spent


class TestRealtimePath:
    def test_conversation_is_realtime_and_costs_per_turn(self, client):
        headers = _auth(client)
        r = client.post("/api/conversation/start", headers=headers, json={"minutes": 5})
        sid = r.json()["session_id"]
        for msg in ("hola", "que tal", "adios"):
            client.post(f"/api/conversation/{sid}/message", headers=headers,
                        json={"content": msg})
        client.post(f"/api/conversation/{sid}/end", headers=headers)

        report = client.get("/api/costs", headers=headers).json()
        conv = [r for r in report["by_activity"]
                if r["activity_type"] == "conversation"][0]
        assert conv["analysis_mode"] == "realtime_analysis"
        # opener(1) + 3 turns + assess(1) = 5 — the many-calls signature.
        assert conv["calls"] == 5
        assert report["realtime_calls"] == 5

    def test_batch_is_cheaper_than_realtime_for_same_work(self, client):
        """The whole point: a structured session costs one call where a live
        one costs many."""
        headers = _auth(client)
        # batch
        r = client.get("/api/recommendation", headers=headers, params={"minutes": 5})
        client.post("/api/session-summary", headers=headers, json={
            "activity_type": "flashcard", "concepts": ["a", "b"], "mistakes": [],
            "confidence": 0.7, "duration_seconds": 60, "difficulty": 0.4,
            "session_id": r.json()["session_id"],
        })
        # realtime
        r = client.post("/api/conversation/start", headers=headers, json={"minutes": 5})
        sid = r.json()["session_id"]
        client.post(f"/api/conversation/{sid}/message", headers=headers,
                    json={"content": "hola"})
        client.post(f"/api/conversation/{sid}/end", headers=headers)

        report = client.get("/api/costs", headers=headers).json()
        assert report["batch_calls"] < report["realtime_calls"]


def test_billable_flag_false_under_mock_backend(client):
    headers = _auth(client)
    r = client.get("/api/recommendation", headers=headers, params={"minutes": 5})
    client.post("/api/session-summary", headers=headers, json={
        "activity_type": "flashcard", "concepts": ["a"], "mistakes": [],
        "confidence": 0.7, "duration_seconds": 60, "difficulty": 0.4,
        "session_id": r.json()["session_id"],
    })
    # Mock backend spends no credits, even though it logically made 1 call.
    report = client.get("/api/costs", headers=headers).json()
    assert report["total_ai_calls"] >= 1
    assert report["total_billable_calls"] == 0
