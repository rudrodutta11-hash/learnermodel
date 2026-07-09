# The Teacher Brain

Kai teaches. The Teacher Brain decides.

The journal (`docs/kai.md`) is the teacher's **memory** — what happened.
The Brain is the teacher's **foresight** — what will happen, and whether he
was right. Same source (the learner model), opposite direction in time.
It is the next natural evolution of the journal, not a new pillar.

## The loop, bracketing every session

```
  session start ──▶ predict()   read the learner model, commit to typed
                                predictions (confidence + evidence, outcome
                                blank). Kai teaches WITH these in front of him.
       │
   (the session happens — Kai teaches to the Brain's read)
       │
  session end ───▶ resolve()    check each open prediction against what
                                actually happened: right / wrong / not-yet-
                                testable (carried to a later session).
       │
                   calibrate()  running hit-rate per prediction kind, for
                                THIS learner. The number that grows.
```

## A prediction

Every prediction carries exactly what the milestone asked for:

| Field | Meaning |
|---|---|
| **kind** | one of five typed reads (below) |
| **statement** | the forecast in plain words |
| **confidence** | how sure the Brain is, 0–1 |
| **evidence** | why it believes this — pulled from the learner model |
| **outcome** | `correct` (True / False / None), filled at session end |

The five kinds map to the milestone's examples:

- `ready_for_challenge` — ready for more difficulty
- `needs_confidence_first` — needs a win before difficulty
- `retains_better_via` — will retain more through a specific format tonight
- `frustration_risk` — likely to be frustrated by a specific topic today
- `near_breakthrough` — close to locking in a stubborn concept

## Why this is architecture, not machine learning (yet)

Generation and evaluation are **deterministic rules over the learner
model** — `teacher_brain/brain.py`, pure and testable, zero API cost. There
is no training, no gradient, no weights. What accumulates is a **track
record**: for each learner, how often each kind of prediction has proven
true. That record is stored, honest, and growing — exactly the substrate a
smarter policy (or a model) can sit on top of later, without re-plumbing
anything.

An untestable prediction (the format it was about wasn't used tonight; the
rough topic never came up) is **carried forward**, not guessed — and if it
never becomes testable, it's set aside and excluded from the hit-rate. The
Brain only scores itself on decisive outcomes. That honesty is what makes
the calibration number mean something.

## What consumes the predictions

- **Kai, now.** Open predictions flow into his system prompt as "Your read
  going in" — so he *teaches* to them: pushes when the read is "ready,"
  engineers a visible win when it's "protect confidence." This is the
  immediate, highest-value consumer.
- **The learner / developer, now.** `GET /api/brain` exposes the open
  predictions and the calibration — the transparency window on the layer
  above Kai.
- **The recommendation engine, eventually.** `prediction_calibration()`
  is the documented seam. Once a learner's `ready_for_challenge` reads are,
  say, 85% accurate, the engine can trust them to nudge difficulty; a kind
  that keeps missing gets discounted. Deliberately **not** wired into engine
  scoring yet — the track record has to exist before it's worth trusting,
  and the engine stays stable until then.

## Where it lives

| Piece | Location |
|---|---|
| Pure domain (kinds, generation, resolution, calibration) | `teacher_brain/brain.py` |
| Persistence (`teacher_predictions`, updated in place on resolve) | `app/db.py` |
| Wiring (make at start, resolve at end, calibration) | `app/state.py` |
| Surfaced to Kai | `app/persona.py` ("Your read going in") |
| Exposed to the world | `GET /api/brain` |
