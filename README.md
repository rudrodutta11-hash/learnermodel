# LearnerModel

An adaptive AI teacher. We do not model what a user knows — **we model how
a user learns.** See [vision.md](vision.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

## What's here (MVP / Version 1)

The goal of V1 is to prove that users prefer an adaptive AI teacher over a
traditional course.

- `learner_model/` — the universal learner model (memory, modality, focus,
  confidence, errors, challenge) + knowledge state + session history
- `recommendation/` — the engine that answers *"what is the highest-impact
  thing this learner can do in the next few minutes?"*
- `experience/` — pluggable delivery formats (mini lesson, flashcards,
  quiz, writing, conversation, story), all voiced by one AI teacher
- `app/` — FastAPI MVP: auth, onboarding, the "how much time do you have?"
  dashboard, one recommendation, session summaries, the AI teacher (Kai)
- `tests/` — model, engine, and API tests
- `demo.py` — offline simulation showing the model learning a learner and
  transferring across subjects

## Run it

```bash
pip install -e ".[app]"
export ANTHROPIC_API_KEY=sk-...   # optional; falls back to a scripted teacher
uvicorn app.main:app --reload
# open http://localhost:8000
```

## Test it

```bash
pip install -e ".[dev]"
pytest
python demo.py   # watch the learner model adapt, then transfer subjects
```
