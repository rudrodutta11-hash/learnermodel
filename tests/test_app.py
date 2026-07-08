"""End-to-end API test: register → onboard → recommend → summarize."""
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEARNER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Re-import with the patched data dir
    import importlib
    from app import db
    importlib.reload(db)
    from app import main
    importlib.reload(main)
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


def test_auth_required(client):
    assert client.get("/api/recommendation", params={"minutes": 5}).status_code == 401


def test_bad_login(client):
    auth_headers(client)
    r = client.post("/api/login", json={"email": "a@b.co", "password": "wrongpass1"})
    assert r.status_code == 401
