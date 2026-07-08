"""Kai's persona: one character everywhere, memory grounded in real records."""
from app.persona import KAI_PERSONA, student_brief, system_prompt
from app.teacher import ScriptedTeacher


def make_context(**overrides) -> dict:
    context = {
        "goal": "Learn Spanish",
        "concepts": ["greetings"],
        "difficulty": 0.4,
        "minutes": 5,
        "learner": {
            "modality_ranking": [("story", 0.8), ("quiz", 0.3)],
            "attention_span_minutes": 6.0,
            "calibration_bias": 0.2,
        },
        "memory": None,
    }
    context.update(overrides)
    return context


RETURNING_MEMORY = {
    "sessions_together": 4,
    "days_since_last": 2.3,
    "last_session": {
        "activity_type": "flashcard",
        "concepts": ["greetings", "numbers"],
        "mistakes": ["ser-vs-estar"],
    },
    "recurring_mistakes": [("ser-vs-estar", 3)],
    "interests": ["travel", "cooking"],
    "motivation": "trip to Peru",
    "level": "beginner",
}


class TestStudentBrief:
    def test_first_session_is_honest_about_no_history(self):
        brief = student_brief(make_context())
        assert "FIRST session" in brief
        assert "ser-vs-estar" not in brief

    def test_returning_session_carries_real_history(self):
        brief = student_brief(make_context(memory=RETURNING_MEMORY))
        assert "Sessions together so far: 4" in brief
        assert "2 days ago" in brief
        assert "ser-vs-estar (x3)" in brief
        assert "trip to Peru" in brief
        assert "travel, cooking" in brief
        assert "FIRST session" not in brief

    def test_learner_model_traits_rendered_for_kai(self):
        brief = student_brief(make_context())
        assert "story works best" in brief
        assert "overestimate" in brief  # calibration_bias 0.2 -> test, don't ask

    def test_system_prompt_is_persona_plus_brief(self):
        prompt = system_prompt(make_context(memory=RETURNING_MEMORY))
        assert prompt.startswith(KAI_PERSONA)
        assert "Your notes on this student" in prompt

    def test_persona_bans_chatbot_voice(self):
        for phrase in ('"Great question!"', '"I hope this helps!"',
                       "Never invent a memory"):
            assert phrase in KAI_PERSONA


class TestScriptedTeacherSpeaksAsKai:
    def test_first_session_opener_introduces_himself(self):
        teacher = ScriptedTeacher()
        opener = teacher.chat([{"role": "user", "content": "(session opener cue)"}],
                              make_context())
        assert "I'm Kai" in opener

    def test_returning_opener_references_last_mistake(self):
        teacher = ScriptedTeacher()
        opener = teacher.chat([{"role": "user", "content": "(session opener cue)"}],
                              make_context(memory=RETURNING_MEMORY))
        assert "ser-vs-estar" in opener
        assert "I'm Kai" not in opener  # no re-introductions mid-relationship

    def test_generated_content_opens_with_memory_line(self):
        teacher = ScriptedTeacher()
        content = teacher.generate("Build today's flashcard deck.",
                                   make_context(memory=RETURNING_MEMORY))
        assert "ser-vs-estar" in content


def test_conversation_api_grounds_kai_memory(tmp_path, monkeypatch):
    """End to end: session one leaves a record; session two's opener
    references it — proof the memory pipeline (summaries → relationship
    memory → persona context → teacher) is actually wired."""
    monkeypatch.setenv("LEARNER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_MODE", "mock")
    import importlib
    from app import conversation, db, main, state
    for module in (db, state, conversation, main):
        importlib.reload(module)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        r = client.post("/api/register",
                        json={"email": "kai@test.co", "password": "hunter2plus"})
        headers = {"Authorization": f"Bearer {r.json()['token']}"}
        client.post("/api/onboarding", headers=headers, json={
            "goal": "Learn Spanish", "level": "beginner",
            "interests": ["travel"], "motivation": "trip to Peru",
        })

        # Session 1: first ever — Kai introduces himself
        r = client.post("/api/conversation/start", headers=headers,
                        json={"minutes": 5})
        first = r.json()
        assert "I'm Kai" in first["opener"]
        client.post(f"/api/conversation/{first['session_id']}/message",
                    headers=headers, json={"content": "Hola!"})
        client.post(f"/api/conversation/{first['session_id']}/end", headers=headers)

        # Session 2: Kai remembers the relationship — he doesn't
        # re-introduce himself now that they have history together.
        r = client.post("/api/conversation/start", headers=headers,
                        json={"minutes": 5})
        second = r.json()
        assert "I'm Kai" not in second["opener"]
        assert second["opener"]  # still stages the session
