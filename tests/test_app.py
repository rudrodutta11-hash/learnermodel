"""End-to-end API test: register → onboard → recommend → summarize."""
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEARNER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Re-import with the patched data dir (order matters: db → state →
    # conversation → main, following the import graph)
    import importlib
    from app import conversation, db, main, state
    for module in (db, state, conversation, main):
        importlib.reload(module)
    with TestClient(main.app) as c:
        yield c


def auth_headers(client) -> dict:
    r = client.post("/api/register", json={"email": "a@b.co", "password": "hunter2plus"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_full_learning_loop(client):
    headers = auth_headers(client)

    # Onboarding creates the initial learner profile
    r = client.post("/api/onboarding", headers=headers, json={
        "goal": "Learn Spanish", "level": "beginner",
        "interests": ["travel"], "weekly_minutes": 90, "motivation": "trip to Peru",
    })
    assert r.status_code == 200

    # Dashboard question: "how much time do you have?" → ONE recommendation
    r = client.get("/api/recommendation", headers=headers, params={"minutes": 5})
    assert r.status_code == 200
    body = r.json()
    rec = body["recommendation"]
    assert rec["activity_type"] in {
        "mini_lesson", "flashcards", "quiz", "writing", "conversation",
        "flashcard", "story",
    }
    assert rec["estimated_minutes"] <= 5
    assert rec["explanation"]
    if rec["activity_type"] == "conversation":
        # Interactive: content comes live from /api/conversation/start
        assert body["experience"]["interaction"] == "chat"
    else:
        assert body["experience"]["content"]  # teacher generated something

    # Session summary feeds the learner model
    r = client.post("/api/session-summary", headers=headers, json={
        "activity_type": "flashcard",
        "concepts": ["greetings", "numbers"],
        "mistakes": ["numbers"],
        "confidence": 0.6,
        "duration_seconds": 300,
        "difficulty": 0.4,
    })
    assert r.status_code == 200
    summary = r.json()
    assert summary["estimated_mastery"] is not None
    assert summary["learner_summary"]["events_observed"] == 2

    # The model remembers: next recommendation sees the weak concept
    r = client.get("/api/me", headers=headers)
    assert r.json()["learner"]["memory"]["items_tracked"] == 2


def test_conversation_loop(client):
    """Open app → 5 minutes → talk with Kai → end → model updated → next rec."""
    headers = auth_headers(client)
    client.post("/api/onboarding", headers=headers, json={
        "goal": "Learn Spanish", "level": "beginner",
        "interests": ["travel"], "weekly_minutes": 90, "motivation": "trip",
    })

    # Start: Kai opens the conversation around engine-chosen concepts
    r = client.post("/api/conversation/start", headers=headers, json={"minutes": 5})
    assert r.status_code == 200, r.text
    start = r.json()
    session_id = start["session_id"]
    assert start["opener"]
    assert start["explanation"]

    # Two learner turns; Kai replies to each and every message is persisted
    for text in ["Hola! Me llamo Sam.", "Yo quiero aprender los saludos."]:
        r = client.post(f"/api/conversation/{session_id}/message",
                        headers=headers, json={"content": text})
        assert r.status_code == 200, r.text
        assert r.json()["reply"]

    user_id = _user_id(client, headers)
    from app import events as events_log

    # Every message is an immutable event: opener + 2 × (learner + teacher)
    logged = events_log.replay(user_id, session_id)
    turn_events = [e for e in logged
                  if e["event_type"] in ("user_message", "teacher_message")]
    assert len(turn_events) == 5
    assert turn_events[0]["event_type"] == "teacher_message"  # opener first

    # recommendation_created and activity_started were logged at start()
    assert {e["event_type"] for e in logged} >= {
        "recommendation_created", "activity_started",
        "user_message", "teacher_message",
    }
    before_end_count = len(logged)

    # End: assessment feeds the learner model, and we get the next rec
    r = client.post(f"/api/conversation/{session_id}/end", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    summary = body["summary"]
    assert summary["concepts_practiced"]
    assert summary["estimated_mastery"] is not None
    assert summary["recommended_review_at"] is not None
    assert summary["learner_summary"]["events_observed"] > 0
    next_rec = body["next_recommendation"]
    assert next_rec["activity_type"] and next_rec["explanation"]

    # End appended events — it never rewrote what was already there.
    after_end = events_log.replay(user_id, session_id)
    assert after_end[:before_end_count] == logged  # untouched prefix
    assert len(after_end) > before_end_count

    # mistake_detected isn't guaranteed here: the mock teacher's assess()
    # fallback reports zero mistakes for a scripted transcript. The other
    # three lifecycle events always fire on completion.
    new_types = {e["event_type"] for e in after_end[before_end_count:]}
    assert new_types >= {
        "activity_completed", "session_summarized", "learner_profile_updated",
    }

    # The session is closed: further messages and double-end are rejected
    r = client.post(f"/api/conversation/{session_id}/message",
                    headers=headers, json={"content": "hola?"})
    assert r.status_code == 409
    assert client.post(f"/api/conversation/{session_id}/end",
                       headers=headers).status_code == 409


def _user_id(client, headers) -> str:
    token = headers["Authorization"].removeprefix("Bearer ")
    from app import auth
    return auth.user_id_for_token(token)


def test_event_log_covers_non_conversation_activities(client):
    """recommendation_created / activity_started / mistake_detected /
    activity_completed / session_summarized / learner_profile_updated all
    fire for the non-interactive activity path too, and GET /api/events
    replays them in stable, append-only order."""
    headers = auth_headers(client)
    client.post("/api/onboarding", headers=headers, json={
        "goal": "Learn Spanish", "level": "beginner",
    })
    r = client.get("/api/recommendation", headers=headers, params={"minutes": 5})
    rec_body = r.json()
    session_id = rec_body["session_id"]
    activity_type = rec_body["recommendation"]["activity_type"]
    if activity_type == "conversation":
        pytest.skip("engine chose the conversation experience for this run")

    client.post("/api/session-summary", headers=headers, json={
        "activity_type": activity_type,
        "concepts": ["greetings"],
        "mistakes": ["greetings"],
        "confidence": 0.5,
        "duration_seconds": 120,
        "difficulty": 0.4,
        "session_id": session_id,
    })

    r = client.get("/api/events", headers=headers, params={"session_id": session_id})
    assert r.status_code == 200
    events = r.json()["events"]
    types = {e["event_type"] for e in events}
    assert types == {
        "recommendation_created", "activity_started", "mistake_detected",
        "activity_completed", "session_summarized", "learner_profile_updated",
    }
    # Events are strictly ordered by id (insertion order) and never mutate.
    assert [e["id"] for e in events] == sorted(e["id"] for e in events)
    r2 = client.get("/api/events", headers=headers, params={"session_id": session_id})
    assert r2.json()["events"] == events


def test_conversation_requires_own_session(client):
    headers = auth_headers(client)
    client.post("/api/onboarding", headers=headers, json={
        "goal": "Spanish", "level": "beginner",
    })
    r = client.post("/api/conversation/nope/message", headers=headers,
                    json={"content": "hi"})
    assert r.status_code == 404


def test_auth_required(client):
    assert client.get("/api/recommendation", params={"minutes": 5}).status_code == 401
    assert client.post("/api/conversation/start",
                       json={"minutes": 5}).status_code == 401


def test_bad_login(client):
    auth_headers(client)
    r = client.post("/api/login", json={"email": "a@b.co", "password": "wrongpass1"})
    assert r.status_code == 401
