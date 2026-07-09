"""The Teacher Brain: predict → resolve → calibrate, and the API loop."""
import importlib

import pytest
from fastapi.testclient import TestClient

from learner_model import LearnerProfile
from learner_model.events import InteractionEvent, Modality
from learner_model.knowledge import KnowledgeState
from teacher_brain import PredictionKind, SessionOutcome, TeacherBrain

NOW = 1_000_000.0


def _ks():
    return KnowledgeState(subject="primary")


class TestGeneration:
    def test_high_success_predicts_ready_for_challenge(self):
        p = LearnerProfile(learner_id="u")
        p.challenge.success_rate = 0.9
        p.challenge.frustration_tolerance = 0.6
        kinds = {pr.kind for pr in TeacherBrain().predict(p, _ks(), NOW)}
        assert PredictionKind.READY_FOR_CHALLENGE in kinds
        assert PredictionKind.NEEDS_CONFIDENCE_FIRST not in kinds  # mutually exclusive

    def test_low_success_predicts_needs_confidence(self):
        p = LearnerProfile(learner_id="u")
        p.challenge.success_rate = 0.4
        preds = TeacherBrain().predict(p, _ks(), NOW)
        assert any(pr.kind is PredictionKind.NEEDS_CONFIDENCE_FIRST for pr in preds)

    def test_every_prediction_carries_confidence_and_evidence(self):
        p = LearnerProfile(learner_id="u")
        p.challenge.success_rate = 0.9
        p.challenge.frustration_tolerance = 0.6
        for pr in TeacherBrain().predict(p, _ks(), NOW):
            assert 0.0 <= pr.confidence <= 1.0
            assert pr.evidence                    # the "why"
            assert pr.correct is None             # outcome filled later
            assert not pr.resolved

    def test_dominant_modality_predicts_retention_channel(self):
        p = LearnerProfile(learner_id="u")
        # Make STORY clearly dominant over everything else.
        for _ in range(20):
            p.modality.observe(Modality.STORY, True, True)
            p.modality.observe(Modality.QUIZ, False, True)
        preds = TeacherBrain().predict(p, _ks(), NOW)
        via = [pr for pr in preds if pr.kind is PredictionKind.RETAINS_BETTER_VIA]
        assert via and via[0].subject_ref == "story"

    def test_stubborn_low_tolerance_error_predicts_frustration(self):
        p = LearnerProfile(learner_id="u")
        p.challenge.frustration_tolerance = 0.2
        for _ in range(3):
            p.errors.observe("ser-vs-estar", "primary", NOW)
        preds = TeacherBrain().predict(p, _ks(), NOW)
        fr = [pr for pr in preds if pr.kind is PredictionKind.FRUSTRATION_RISK]
        assert fr and fr[0].subject_ref == "ser-vs-estar"


class TestResolution:
    def _pred(self, kind, ref=""):
        from teacher_brain.brain import Prediction
        return Prediction(kind=kind, statement="x", confidence=0.7, evidence="e",
                          subject_ref=ref)

    def test_ready_confirmed_when_they_handle_it(self):
        pr = self._pred(PredictionKind.READY_FOR_CHALLENGE)
        TeacherBrain().resolve(pr, SessionOutcome(
            engaged=True, success=0.8, difficulty=0.6, modality="quiz"), NOW)
        assert pr.resolved and pr.correct is True

    def test_ready_wrong_when_they_check_out(self):
        pr = self._pred(PredictionKind.READY_FOR_CHALLENGE)
        TeacherBrain().resolve(pr, SessionOutcome(
            engaged=False, success=0.3, difficulty=0.6, modality="quiz"), NOW)
        assert pr.resolved and pr.correct is False

    def test_retention_untestable_on_different_modality_carries(self):
        pr = self._pred(PredictionKind.RETAINS_BETTER_VIA, ref="story")
        TeacherBrain().resolve(pr, SessionOutcome(
            engaged=True, success=0.9, difficulty=0.5, modality="quiz"), NOW)
        # Couldn't test (wrong format) — stays open, carried forward.
        assert not pr.resolved and pr.correct is None and pr.sessions_carried == 1

    def test_untestable_prediction_expires_after_max_carry(self):
        pr = self._pred(PredictionKind.NEAR_BREAKTHROUGH, ref="ser-vs-estar")
        brain = TeacherBrain()
        untouched = SessionOutcome(engaged=True, success=1.0, difficulty=0.5,
                                   modality="quiz", concepts=(), mistakes=())
        for _ in range(4):
            brain.resolve(pr, untouched, NOW)
        assert pr.resolved and pr.correct is None  # set aside, excluded from hit-rate

    def test_frustration_confirmed_when_topic_trips_them(self):
        pr = self._pred(PredictionKind.FRUSTRATION_RISK, ref="ser-vs-estar")
        TeacherBrain().resolve(pr, SessionOutcome(
            engaged=True, success=0.5, difficulty=0.5, modality="quiz",
            concepts=("ser-vs-estar",), mistakes=("ser-vs-estar",)), NOW)
        assert pr.correct is True


class TestCalibration:
    def test_hit_rate_accumulates_per_kind(self):
        from teacher_brain.brain import Prediction
        brain = TeacherBrain()
        preds = []
        for correct in (True, True, False):
            p = Prediction(kind=PredictionKind.READY_FOR_CHALLENGE,
                           statement="x", confidence=0.7, evidence="e")
            p.resolved, p.correct = True, correct
            preds.append(p)
        untestable = Prediction(kind=PredictionKind.NEAR_BREAKTHROUGH,
                                statement="x", confidence=0.5, evidence="e")
        untestable.resolved, untestable.correct = True, None
        preds.append(untestable)

        cal = brain.calibration(preds)
        assert cal["decided"] == 3                       # the None is excluded
        assert cal["by_kind"]["ready_for_challenge"]["accuracy"] == round(2 / 3, 3)
        assert cal["overall_accuracy"] == round(2 / 3, 3)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEARNER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_MODE", "mock")
    from app import conversation, db, main, state
    for module in (db, state, conversation, main):
        importlib.reload(module)
    with TestClient(main.app) as c:
        yield c


def _seed_ready_learner(client, headers):
    """Push the learner model to a state where the Brain predicts readiness,
    by feeding a run of confident correct sessions."""
    for i in range(6):
        client.post("/api/session-summary", headers=headers, json={
            "activity_type": "flashcard", "concepts": [f"c{i}"],
            "mistakes": [], "confidence": 0.8, "duration_seconds": 60,
            "difficulty": 0.5,
        })


class TestBrainAPI:
    def _auth(self, client):
        r = client.post("/api/register",
                        json={"email": "b@r.co", "password": "hunter2plus"})
        headers = {"Authorization": f"Bearer {r.json()['token']}"}
        client.post("/api/onboarding", headers=headers, json={
            "goal": "Learn Spanish", "level": "beginner",
            "interests": ["travel"], "motivation": "trip",
        })
        return headers

    def test_predictions_open_then_resolve_across_a_session(self, client):
        headers = self._auth(client)
        _seed_ready_learner(client, headers)  # → Brain will read "ready for challenge"

        # Starting a session opens predictions (the Brain commits to a read)...
        r = client.post("/api/conversation/start", headers=headers, json={"minutes": 5})
        sid = r.json()["session_id"]
        brain = client.get("/api/brain", headers=headers).json()
        assert brain["open_predictions"]

        # ...and ending it resolves them, growing the track record.
        client.post(f"/api/conversation/{sid}/message", headers=headers,
                    json={"content": "Listo para más!"})
        client.post(f"/api/conversation/{sid}/end", headers=headers)

        after = client.get("/api/brain", headers=headers).json()
        assert after["calibration"]["decided"] >= 1   # at least one verdict recorded

    def test_recommendation_preview_defers_prediction_to_session_start(self, client):
        """A conversation recommendation is only a preview — predictions are
        made when the real session starts, not double-generated here."""
        headers = self._auth(client)
        _seed_ready_learner(client, headers)
        r = client.get("/api/recommendation", headers=headers, params={"minutes": 5})
        if r.json()["recommendation"]["activity_type"] == "conversation":
            # Preview only: no predictions committed against this throwaway id.
            assert client.get("/api/brain", headers=headers).json()["open_predictions"] == []
        else:
            # A deliverable activity DID open predictions to resolve at summary.
            assert client.get("/api/brain", headers=headers).json()["open_predictions"]

    def test_predictions_reach_kai_via_context(self, client):
        """The Brain's read is in Kai's system prompt — he teaches to it."""
        from app import state
        from app.persona import system_prompt
        headers = self._auth(client)
        _seed_ready_learner(client, headers)
        user_id = _uid(client, headers)

        state.make_predictions(user_id, "sess-x")
        preds = state.open_predictions(user_id)
        assert preds
        context = {"goal": "Spanish", "predictions": [
            {"statement": p.statement, "confidence": p.confidence,
             "evidence": p.evidence} for p in preds]}
        prompt = system_prompt(context)
        assert "Your read going in" in prompt
        assert preds[0].statement in prompt


def _uid(client, headers):
    from app import auth
    return auth.user_id_for_token(headers["Authorization"].removeprefix("Bearer "))
