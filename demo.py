"""Offline demo: watch the learner model learn HOW a person learns,
then transfer that understanding to a brand-new subject.

We simulate "Maya": forgets fast, thrives on stories, hates quizzes,
focuses ~8 minutes. After six simulated weeks of Spanish, she opens
History for the first time — and the recommendation engine already
teaches her the way she learns.

Run: python demo.py
"""
import random
import time

from learner_model import InteractionEvent, LearnerProfile, Modality
from learner_model.knowledge import Concept, KnowledgeState
from recommendation import RecommendationEngine, SessionContext

HOUR, DAY = 3600.0, 86400.0
rng = random.Random(7)

# Maya's ground truth (hidden from the model — it must discover this)
TRUE_HALF_LIFE_H = 10.0          # fast forgetter
MODALITY_SKILL = {Modality.STORY: 0.92, Modality.CONVERSATION: 0.8,
                  Modality.FLASHCARD: 0.65, Modality.QUIZ: 0.4}

profile = LearnerProfile(learner_id="maya")
profile.modality.rng = rng
spanish = KnowledgeState(subject="spanish")
spanish.add_concepts([
    Concept(id=f"unit-{i}", subject="spanish", name=f"Unit {i}",
            difficulty=0.2 + 0.05 * i) for i in range(6)
])
engine = RecommendationEngine()
start = time.time() - 42 * DAY

print("=== Six weeks of Spanish ===")
now = start
units = list(spanish.concepts.values())
for day in range(42):
    now = start + day * DAY
    session = f"day{day}"
    for k in range(4):  # daily study rotation
        modality = rng.choice(list(MODALITY_SKILL))
        concept = units[(day * 4 + k) % len(units)]
        item = profile.memory.items.get(concept.key)
        recall = 2 ** (-max(0.0, (now - item.last_review_ts) / 3600) / TRUE_HALF_LIFE_H) if item else 0.5
        # Recall outcome is driven by Maya's true memory; whether she stays
        # engaged is driven by how well the teaching format suits her.
        correct = rng.random() < max(0.1, recall)
        profile.update(InteractionEvent(
            learner_id="maya", subject="spanish", item_id=concept.id,
            modality=modality, correct=correct, confidence=min(1.0, recall + 0.2),
            difficulty=concept.difficulty, duration_seconds=150,
            engaged=rng.random() < MODALITY_SKILL[modality],
            error_signature="ser-vs-estar" if (not correct and rng.random() < 0.3) else None,
            timestamp=now + rng.random() * HOUR, session_id=session,
        ))

summary = profile.summary()
print(f"Events observed:        {summary['events_observed']}")
print(f"Base half-life learned: {summary['memory']['base_half_life_hours']}h "
      f"(true tendency: fast forgetter)")
print(f"Attention span:         {summary['attention_span_minutes']} min")
print(f"Calibration bias:       {summary['calibration_bias']} (+ = overconfident)")
print("Modality ranking (discovered):")
for name, score in summary["modality_ranking"]:
    print(f"  {name:<14} {score:.2f}")

print("\n=== Day one of History — same profile, new subject ===")
history_ks = KnowledgeState(subject="history")
history_ks.add_concepts([
    Concept(id="ww1-causes", subject="history", name="Causes of WW1", difficulty=0.4),
    Concept(id="ww1-timeline", subject="history", name="WW1 timeline", difficulty=0.5,
            prerequisites=("ww1-causes",)),
    Concept(id="treaty-versailles", subject="history", name="Treaty of Versailles",
            difficulty=0.6, prerequisites=("ww1-timeline",)),
])
rec = engine.recommend(profile, history_ks,
                       SessionContext(subject="history", available_minutes=10,
                                      goal="Understand WW1", now_ts=now))
print(f"Recommended: {rec.activity_type.value} @ difficulty {rec.difficulty} "
      f"for {rec.estimated_minutes} min")
print(f"Concepts:    {list(rec.concepts)}")
print(f"Why:         {rec.explanation}")
print("\nNo cold start. The AI never had to relearn Maya.")
