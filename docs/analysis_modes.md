# Analysis Modes — the cost architecture

**The principle: not every interaction needs a real-time AI call.**

Each activity type declares how it uses the AI. Two modes:

| Mode | AI involvement | Cost signature | Used for |
|---|---|---|---|
| `realtime_analysis` | in the loop every turn | many calls per session | Kai conversations, live writing feedback, calls |
| `batch_analysis` | one call, at the very end | **1 call per session** | flashcards, quizzes, drills, reading checks, structured lessons |

The mapping lives in `app/analysis.py` (`ANALYSIS_MODE`). It is the single
source of truth, used three ways: to route the end-of-session analysis, to
steer format choice under a cheap cost policy, and to label the cost ledger.

## How batch saves money

A structured activity (say, a 30-card flashcard drill) runs entirely on
**scripted/local logic** — the client grades each card, no AI. At the end it
sends **compact structured results**, not the raw card-by-card history:

```
{ activity_type, concepts, mistakes,
  counts: {attempted, correct},
  missed_items: [ ...only the stumbles, capped at 8... ] }
```

The server makes **exactly one** `analyze_session` call on that payload,
producing the session summary, mistakes, mastery signals, teaching-journal
entry, and next-recommendation signals — everything a live session gets from
its turns, in a single call. One end call replaces the dozens of small calls
a naive "grade each card with AI" design would make. That is the saving.

By contrast a conversation is `realtime_analysis`: opener + one call per turn
+ a final assessment. A 3-turn chat is 5 AI calls; the equivalent structured
session is 1.

## The cost ledger

Every finished activity writes a row to `activity_costs`:
`activity_type`, `analysis_mode`, `ai_calls`, and `billable` (0 when the
scripted/mock backend served it — no real credits). `GET /api/costs` rolls
it up:

```json
{ "realtime_calls": 4, "batch_calls": 1,
  "total_ai_calls": 5, "total_billable_calls": 0,
  "by_activity": [ {activity_type, analysis_mode, calls, sessions, billable_calls} ] }
```

So spend is always attributable: which formats, which mode, how many calls,
how many actually billable.

## Cheap mode prefers batch

`cost_prefers_batch()` is tied to `AI_MODE`: in `mock` (free dev) and `cheap`
(Haiku) it returns true; in `premium` it returns false. When true, the
recommendation engine applies a small penalty (`BATCH_PREFERENCE_FACTOR`,
0.8) to realtime activities — enough to break ties toward batch formats when
live adaptation isn't essential, **never** enough to suppress a conversation
the learner clearly needs, and never applied to a conversation the learner
explicitly starts. Pedagogy still wins when it clearly should; the nudge only
decides the close calls.

## What's out of scope (on purpose)

**Content generation** (building the lesson/deck/quiz) is a *delivery* cost,
not an *analysis* cost. It's cacheable and amortizable per concept rather
than per session ("pre-generated content"), so it's not counted in the
analysis ledger. The request was about analysis calls, and that's what the
ledger tracks.
