"""The learner-experience milestone: pacing, variety, home screen, cliffhanger."""
import random
import time

import pytest
from fastapi.testclient import TestClient

from app.conversation import _phase_note
from learner_model import LearnerProfile
from learner_model.knowledge import Concept, KnowledgeState
from recommendation import RecommendationEngine, SessionContext
from recommendation.preview import BANNED_WORDS


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEARNER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_MODE", "mock")
    import importlib
    from app import conversation, db, main, state
    for module in (db, state, conversation, main):
        importlib.reload(module)
    with TestClient(main.app) as c:
        yield c


def auth_headers(client) -> dict:
    r = client.post("/api/register", json={"email": "x@y.co", "password": "hunter2plus"})
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    client.post("/api/onboarding", headers=headers, json={
        "goal": "Learn Spanish", "level": "beginner",
        "interests": ["travel"], "motivation": "trip to Peru",
    })
    return headers


class TestSessionArc:
    """Conversations pace like a real session: open fast, work, LAND."""

    def test_phases_cover_the_whole_arc(self):
        assert "Opening" in _phase_note(0.5, 5)
        assert "Mid-session" in _phase_note(2.5, 5)
        assert "landing" in _phase_note(4.2, 5).lower()
        assert "wrap NOW" in _phase_note(9.0, 5)

    def test_mock_kai_lands_the_ending(self, client):
        headers = auth_headers(client)
        r = client.post("/api/conversation/start", headers=headers,
                        json={"minutes": 0.005})  # 0.3s budget → overtime fast
        sid = r.json()["session_id"]
        time.sleep(0.5)
        r = client.post(f"/api/conversation/{sid}/message", headers=headers,
                        json={"content": "hola"})
        reply = r.json()["reply"]
        assert "that's where we stop" in reply.lower() or "stop" in reply.lower()


class TestVariety:
    """Same format twice in a row must earn its repeat."""

    def rec(self, recent=()):
        profile = LearnerProfile(learner_id="u1")
        profile.modality.rng = random.Random(7)
        ks = KnowledgeState(subject="spanish")
        ks.add_concepts([Concept(id="greetings", subject="spanish", name="Greetings")])
        return RecommendationEngine().recommend(profile, ks, SessionContext(
            subject="spanish", available_minutes=10,
            recent_activity_types=tuple(recent),
        ))

    def test_repeat_is_penalized(self):
        first = self.rec()
        repeat = self.rec(recent=[first.activity_type.value])
        # Either the engine switched formats, or the repeated format's
        # score paid the penalty — both are strictly lower than before.
        assert repeat.score < first.score

    def test_unrelated_history_changes_nothing(self):
        first = self.rec()
        other = "call" if first.activity_type.value != "call" else "game"
        assert self.rec(recent=[other]).activity_type == first.activity_type


class TestHome:
    """The dashboard opens mid-story."""

    def test_home_before_any_session(self, client):
        headers = auth_headers(client)
        h = client.get("/api/home", headers=headers).json()
        assert h["chapter"] == 1
        assert h["streak_days"] == 0
        assert h["previously"] is None
        assert h["tonight"]["character"]          # someone is always waiting
        assert h["tonight"]["returning"] is False

    def test_home_after_a_session_recaps_and_streaks(self, client):
        headers = auth_headers(client)
        r = client.post("/api/conversation/start", headers=headers, json={"minutes": 5})
        sid = r.json()["session_id"]
        client.post(f"/api/conversation/{sid}/message", headers=headers,
                    json={"content": "Hola!"})
        client.post(f"/api/conversation/{sid}/end", headers=headers)

        h = client.get("/api/home", headers=headers).json()
        assert h["chapter"] == 2
        assert h["streak_days"] == 1
        assert h["previously"] and "Kai" in h["previously"]

    def test_home_copy_never_sounds_educational(self, client):
        headers = auth_headers(client)
        # Run a real session first so the 'previously' recap is populated
        # and scanned too — that's where fallback concept ids leak through.
        r = client.post("/api/conversation/start", headers=headers, json={"minutes": 5})
        sid = r.json()["session_id"]
        client.post(f"/api/conversation/{sid}/message", headers=headers,
                    json={"content": "Hola!"})
        client.post(f"/api/conversation/{sid}/end", headers=headers)

        h = client.get("/api/home", headers=headers).json()
        copy = " ".join(filter(None, [
            h.get("previously") or "", h["tonight"]["hook"], h["tonight"]["role"],
        ])).lower()
        for word in BANNED_WORDS:
            assert word not in copy, f"educational language leaked: {word!r}"


class TestCliffhanger:
    """Every ending points at the next beginning."""

    def test_conversation_end_returns_next_time(self, client):
        headers = auth_headers(client)
        r = client.post("/api/conversation/start", headers=headers, json={"minutes": 5})
        sid = r.json()["session_id"]
        client.post(f"/api/conversation/{sid}/message", headers=headers,
                    json={"content": "Hola!"})
        body = client.post(f"/api/conversation/{sid}/end", headers=headers).json()
        nt = body["next_time"]
        assert nt["character"] and nt["hook"]

    def test_cliffhanger_is_the_real_next_scene(self, client):
        """The hook shown at the credits is what /api/home stages next —
        the promise and the product are the same object."""
        headers = auth_headers(client)
        r = client.post("/api/conversation/start", headers=headers, json={"minutes": 5})
        sid = r.json()["session_id"]
        client.post(f"/api/conversation/{sid}/message", headers=headers,
                    json={"content": "Hola!"})
        nt = client.post(f"/api/conversation/{sid}/end", headers=headers).json()["next_time"]
        tonight = client.get("/api/home", headers=headers).json()["tonight"]
        assert nt["character"] == tonight["character"]
        assert nt["hook"] == tonight["hook"]

    def test_non_conversation_summary_also_hooks_forward(self, client):
        headers = auth_headers(client)
        r = client.post("/api/session-summary", headers=headers, json={
            "activity_type": "flashcard", "concepts": ["greetings"],
            "mistakes": [], "confidence": 0.7,
            "duration_seconds": 120, "difficulty": 0.4,
        })
        assert r.json()["next_time"]["hook"]
