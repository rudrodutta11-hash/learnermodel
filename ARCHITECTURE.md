# Architecture

The system is NOT centered around courses. It revolves around five core
systems. Everything else — including subjects and content — plugs into them.

```
                          ┌──────────────────────────┐
                          │      Session History      │  append-only event log
                          │   (system of record)      │  (every interaction ever)
                          └─────────────┬────────────┘
                                        │ replayable
              ┌─────────────────────────┼─────────────────────────┐
              ▼                         ▼                         │
   ┌────────────────────┐    ┌────────────────────┐              │
   │  Learner Profile   │    │  Knowledge State   │              │
   │  HOW they learn    │    │  WHAT they know    │              │
   │  (universal,       │    │  (per subject,     │              │
   │   permanent)       │    │   perishable)      │              │
   └─────────┬──────────┘    └─────────┬──────────┘              │
             │                         │                         │
             └──────────┬──────────────┘                         │
                        ▼                                        │
             ┌─────────────────────┐                             │
             │ Recommendation      │   "What should this         │
             │ Engine              │    learner do next?"        │
             └─────────┬───────────┘                             │
                       │ Recommendation (WHAT)                   │
                       ▼                                         │
             ┌─────────────────────┐      ┌──────────────┐       │
             │ Experience Engine   │◄─────┤  AI Teacher  │       │
             │ (delivery: HOW)     │      │  (one voice) │       │
             └─────────┬───────────┘      └──────────────┘       │
                       │ InteractionEvents                       │
                       └─────────────────────────────────────────┘
```

## 1. Learner Profile (`learner_model/profile.py`)

One object per human, aggregating HOW they learn. Permanent and
subject-agnostic — six months of Spanish trains it; day one of History
spends it.

| Dimension | Module | Model |
|---|---|---|
| Memory characteristics | `memory.py` | Per-learner adaptive forgetting curve: `R(t) = 2^(-t/half_life)`, with learner-level base half-life and stability gain updated from recall prediction error |
| Preferred teaching styles | `modality.py` | Beta-Bernoulli bandit per modality; Thompson sampling for explore/exploit |
| Focus | `focus.py` | EMA of demonstrated attention span from in-session engagement decay |
| Confidence | `confidence.py` | Calibration bias (self-report vs. correctness) + Brier score |
| Strengths / weaknesses / repeated mistakes | `errors.py` | Recurrence × recency severity over canonical error signatures |
| Challenge tolerance / motivation proxy | `challenge.py` | Personal flow-zone estimate (target success ≈ 80%) + frustration tolerance |

Single write path: `profile.update(event)`. The profile never sees content
— only behavior.

## 2. Knowledge State (`learner_model/knowledge.py`)

WHAT the learner currently knows, per subject. Deliberately separate from
the profile: knowledge is perishable and subject-scoped; the profile is
permanent and universal.

- **Concepts mastered / weak** — derived from retrievability + repetitions
- **Review schedule** — solved analytically from each learner's personal
  forgetting curve (`review_due_ts` = when retrievability hits threshold)
- **Concept relationships** — prerequisite graph; the "frontier" (unseen
  concepts whose prerequisites are mastered) is what's learnable next

Knowledge State is a *view* computed over the memory model + concept
graph. It holds no state of its own that can drift.

## 3. Session History (`learner_model/history.py` + `app/events.py`)

Two append-only logs at different altitudes, both INSERT-only:

- **`learner_model/history.py`** — the math-facing log: one `InteractionEvent`
  per concept per activity (mistakes, activity used, duration, completion,
  confidence, correctness). This is what `profile.update()` consumes, and
  `history.replay_into(profile)` rebuilds a profile from scratch — improve
  the models later and every profile gets smarter retroactively.
- **`app/events.py`** — the product-facing log: every observable step of a
  learner's interaction, typed and timestamped:

  `recommendation_created → activity_started → user_message* / teacher_message*
  → mistake_detected* → activity_completed → session_summarized →
  learner_profile_updated`

  This is the full audit trail — richer than what today's models use (raw
  chat content, every recommendation the engine ever produced, every
  mistake as it was detected). `events.replay(user_id)` reads it back in
  order; a future model version can mine it for signal the current models
  don't yet extract, the same way `InteractionEvent` history lets the core
  math be rebuilt. Neither log has an update or delete path — anywhere.

## 4. Recommendation Engine (`recommendation/engine.py`)

One responsibility: **"What should this learner do next?"** Inputs:
learner profile, knowledge state, session history (via derived state),
available time, goals. Output — the full contract:

```python
Recommendation(
    activity_type,       # which experience format
    difficulty,          # personalized flow-zone target
    concepts,            # what to practice/learn
    explanation,         # why this, why now — shown to the learner
    expected_outcome,    # what this session should achieve
    estimated_minutes,
)
```

Scoring: `need_value × modality_effectiveness (Thompson sample) ×
challenge_fit × time_fit`, over needs generated from decaying memories,
recurring errors, and the learning frontier.

**The engine never generates content.** It decides WHAT; delivery is the
Experience Engine's job. New experience types are added without touching
this module — the only coupling is the `NEED_ACTIVITIES` registry.

## 5. Experience Engine (`experience/`)

Turns a `Recommendation` into something the learner actually does.
Experience types (conversation, writing, flashcards, mini lesson, story,
quiz, roleplay, generated video…) register themselves:

```python
register_experience(MyNewExperience())   # the entire cost of a new format
```

Every experience generates its content through the **AI Teacher**
(`app/teacher.py`) — one consistent persona. The Teacher separates WHAT
is said from HOW it's delivered, so conversational voice (TTS, phone
calls) plugs in later as a new transport without rewriting experiences.

---

## Technology Choices

### MVP (current)

| Layer | Choice | Why |
|---|---|---|
| Core models | Pure Python, zero deps | The learner model is math + state; portability and testability matter most |
| API | FastAPI | Async, typed, fast to iterate |
| LLM | Claude (`claude-opus-4-8`) via Anthropic SDK | Teacher content generation + conversation |
| Storage | SQLite (app) + JSON/JSONL (profiles, history) | Zero-ops; each is behind a narrow seam |
| Frontend | Single static page | The product is the recommendation loop, not the UI |

### Scale path (millions of users)

The seams are already in place; each swap is behind an interface:

| Concern | MVP | At scale |
|---|---|---|
| Session history | JSONL files | Kafka/Kinesis event stream → warehouse (replayable by design) |
| Profiles | JSON files | Postgres/DynamoDB keyed by learner_id; Redis cache for the hot read path |
| Recommendation | In-process | Stateless service; profiles are small (KBs), so it stays cheap and horizontal |
| Experience generation | Sync Claude calls | Queue + streaming; prompt caching on the stable teacher persona |
| Auth | Bearer tokens in SQLite | Managed provider (Clerk/Auth0) — the app only ever sees `user_id` |
| Voice teacher | — | New Teacher transport (TTS/telephony) over the same `chat()` surface |

Two properties make this architecture scale safely:

1. **Event sourcing** — history is the truth; every model improvement is a
   replay, not a migration.
2. **Narrow contracts** — subjects emit `InteractionEvent`s and declare
   `Concept`s; experiences consume `Recommendation`s. Nothing else crosses
   the boundaries, so teams can ship subjects and experience types
   independently of the core.
