"""Episode previews: recommendations must read like the next chapter,
never like a syllabus. The banned-word scan is the contract."""
import random
import re
import time

from learner_model import InteractionEvent, LearnerProfile, Modality
from learner_model.knowledge import Concept, KnowledgeState
from recommendation import RecommendationEngine, SessionContext, episode_preview
from recommendation.preview import BANNED_WORDS

HOUR = 3600.0
NOW = time.time()


def assert_clean(text: str, where: str = "") -> None:
    lowered = text.lower()
    for word in BANNED_WORDS:
        assert not re.search(rf"\b{word}", lowered), (
            f"educational word {word!r} leaked into {where or 'copy'}: {text!r}"
        )


def all_previews():
    """Every need × concept-shape × chapter — the full copy surface."""
    concept_sets = [[], ["past-tense"], ["greetings", "numbers", "past-tense"]]
    memories = [
        None,
        {"sessions_together": 0},
        {"sessions_together": 7,
         "recurring_mistakes": [("ser-vs-estar", 3)]},
    ]
    for need in ("review", "fix_errors", "new_material"):
        for concepts in concept_sets:
            for memory in memories:
                for minutes in (2, 5, 20):
                    yield episode_preview(
                        need=need, concepts=concepts, minutes=minutes,
                        goal="Learn Spanish", memory=memory,
                    )


class TestEpisodePreview:
    def test_no_educational_language_anywhere(self):
        for preview in all_previews():
            for field, text in preview.items():
                assert_clean(text, f"preview.{field}")

    def test_shape_and_hook(self):
        preview = episode_preview(need="review", concepts=["greetings"],
                                  minutes=5, memory={"sessions_together": 3})
        assert set(preview) == {"kicker", "title", "teaser"}
        assert preview["kicker"].startswith("Chapter 4")
        assert preview["title"] and preview["teaser"]

    def test_first_chapter_gets_its_own_kicker(self):
        preview = episode_preview(need="new_material", concepts=[], minutes=5,
                                  memory=None)
        assert "Chapter 1" in preview["kicker"]
        assert "story starts" in preview["kicker"]

    def test_recurring_mistake_becomes_the_villain(self):
        preview = episode_preview(
            need="fix_errors", concepts=["ser-vs-estar"], minutes=5,
            memory={"sessions_together": 5,
                    "recurring_mistakes": [("ser-vs-estar", 3)]},
        )
        blob = (preview["title"] + " " + preview["teaser"]).lower()
        assert "ser vs estar" in blob
        # the defeat count feeds the drama in two of three variants
        assert ("3 times" in blob) or ("returns" in blob) or ("again" in blob)

    def test_deterministic_per_request(self):
        kwargs = dict(need="review", concepts=["greetings"], minutes=5,
                      memory={"sessions_together": 2})
        assert episode_preview(**kwargs) == episode_preview(**kwargs)

    def test_copy_rotates_across_chapters(self):
        titles = {
            episode_preview(need="review", concepts=["greetings"], minutes=5,
                            memory={"sessions_together": n})["title"]
            for n in range(9)
        }
        assert len(titles) > 1  # same beat, fresh pitch as the story advances


class TestEngineCopyIsClean:
    def test_engine_strings_never_use_school_words(self):
        """Exercise all three needs through the real engine and scan the
        learner-facing strings."""
        profile = LearnerProfile(learner_id="u1")
        profile.modality.rng = random.Random(7)
        ks = KnowledgeState(subject="spanish")
        ks.add_concepts([
            Concept(id="greetings", subject="spanish", name="Greetings"),
            Concept(id="numbers", subject="spanish", name="Numbers"),
        ])
        # decayed memory -> review need; repeated error -> fix_errors need
        for concept, ts in (("greetings", NOW - 300 * HOUR), ("numbers", NOW)):
            profile.update(InteractionEvent(
                learner_id="u1", subject="spanish", item_id=concept,
                modality=Modality.FLASHCARD, correct=True, timestamp=ts,
                session_id="s", duration_seconds=30,
            ))
        for i in range(3):
            profile.update(InteractionEvent(
                learner_id="u1", subject="spanish", item_id=f"e{i}",
                modality=Modality.QUIZ, correct=False,
                error_signature="ser-vs-estar", timestamp=NOW,
                session_id="s", duration_seconds=30,
            ))

        engine = RecommendationEngine()
        for _ in range(25):  # Thompson sampling explores all needs over runs
            rec = engine.recommend(profile, ks, SessionContext(
                subject="spanish", available_minutes=10, now_ts=NOW,
            ))
            assert_clean(rec.explanation, "recommendation.explanation")
            assert_clean(rec.expected_outcome, "recommendation.expected_outcome")

    def test_experience_titles_are_clean(self):
        import experience.builtin  # noqa: F401 — populate the registry
        from experience.base import _REGISTRY
        for exp in _REGISTRY.values():
            assert_clean(exp.title, f"experience title ({exp.activity_type})")


def test_api_serves_episode_previews(tmp_path, monkeypatch):
    monkeypatch.setenv("LEARNER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_MODE", "mock")
    import importlib
    from app import conversation, db, main, state
    for module in (db, state, conversation, main):
        importlib.reload(module)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        r = client.post("/api/register",
                        json={"email": "ep@test.co", "password": "hunter2plus"})
        headers = {"Authorization": f"Bearer {r.json()['token']}"}
        client.post("/api/onboarding", headers=headers, json={
            "goal": "Learn Spanish", "level": "beginner",
        })

        r = client.get("/api/recommendation", headers=headers,
                       params={"minutes": 5})
        preview = r.json()["preview"]
        assert set(preview) == {"kicker", "title", "teaser"}
        for field, text in preview.items():
            assert_clean(text, f"api preview.{field}")

        # conversation start carries the same pitch
        r = client.post("/api/conversation/start", headers=headers,
                        json={"minutes": 5})
        start = r.json()
        assert_clean(start["preview"]["teaser"], "conversation preview")
        client.post(f"/api/conversation/{start['session_id']}/message",
                    headers=headers, json={"content": "Hola!"})
        r = client.post(f"/api/conversation/{start['session_id']}/end",
                        headers=headers)
        next_rec = r.json()["next_recommendation"]
        assert "preview" in next_rec
        assert_clean(next_rec["preview"]["teaser"], "next_recommendation preview")
