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
- `story/` — the reusable Story Engine: recurring characters (Carlos the
  taxi driver, María the receptionist, Ana, Diego) whose arcs continue
  across sessions. Conversations continue their story; Kai references what
  happened last time; the learning hides inside the narrative. Cast is
  swappable per subject — see `docs/story.md`
- `app/` — FastAPI MVP: auth, onboarding, the "how much time do you have?"
  dashboard, one recommendation, session summaries, the AI teacher (Kai),
  and the live conversation experience (`/api/conversation/*`): chat with
  Kai, every message logged to the append-only `events` table
  (`app/events.py`), and on end Kai assesses the transcript to update the
  learner model. `GET /api/events` replays a learner's full interaction
  trail — recommendations, activity lifecycle, chat turns, mistakes,
  summaries, profile updates — nothing is ever overwritten. The chat
  transport is a single seam (`sendToKai`/`renderMessage` in the UI, plain
  role+content events in the API) so voice (STT/TTS or a call bridge)
  plugs in later
  without touching session, event, or summary logic.
- `app/persona.py` + `docs/kai.md` — Kai himself: the character bible
  every AI interaction builds its prompt from, and the human-readable
  companion with example conversations. Kai's session memory comes from
  real records only (`state.relationship_memory`) — he never invents
  history
- `teacher_brain/` + `docs/teacher_brain.md` — the layer that DECIDES,
  above the Kai who TEACHES. It predicts what will work for a learner
  (typed predictions with confidence + evidence), then checks at each
  session's end whether it was right, building a per-learner track record.
  Deterministic architecture, not ML yet. Kai teaches to the open
  predictions; `GET /api/brain` exposes them; the recommendation engine
  will consume the calibration eventually.
- `app/analysis.py` + `docs/analysis_modes.md` — the cost architecture.
  Each activity runs `realtime_analysis` (AI every turn — conversations,
  live writing) or `batch_analysis` (scripted/local interaction, then ONE
  compact AI call at the end — flashcards, quizzes, lessons). One batch
  call replaces the many small calls a live-graded activity would make;
  `GET /api/costs` attributes spend by activity_type and analysis_mode, and
  cheap AI modes gently prefer batch formats.
- `tests/` — model, engine, API, persona, Teacher Brain, and cost tests
- `demo.py` — offline simulation showing the model learning a learner and
  transferring across subjects

## Run it

```bash
pip install -e ".[app]"
uvicorn app.main:app --reload
# open http://localhost:8000
```

### AI_MODE — control API spend

The AI teacher's backend is selected by `AI_MODE`:

| `AI_MODE` | Backend | Cost |
|---|---|---|
| `mock` (default) | `ScriptedTeacher` — deterministic, no network | Free |
| `cheap` | Claude Haiku (`claude-haiku-4-5`) | Lowest-cost real model |
| `premium` | Claude Opus (`claude-opus-4-8`) | Production-quality generation |

Default is `mock` so building, testing, and demoing never touches your
credits. Opt into real generation explicitly:

```bash
export ANTHROPIC_API_KEY=sk-...
export AI_MODE=cheap      # or premium
uvicorn app.main:app --reload
```

`cheap`/`premium` silently fall back to `mock` if `ANTHROPIC_API_KEY` is
unset or the client fails to initialize — the app never hard-fails on a
missing key or exhausted credits.

## Test it

```bash
pip install -e ".[dev]"
pytest
python demo.py   # watch the learner model adapt, then transfer subjects
```
