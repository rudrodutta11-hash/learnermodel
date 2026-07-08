# The Story Engine

The learner doesn't come back to practice Spanish. They come back to find
out whether Carlos pulls off his daughter's quinceañera, whether María
ever leaves the city she's never left, whether Diego's restaurant survives
the review. The learning happens *inside* those stories — but what pulls
them back is the story.

This is a reusable narrative layer (`story/`) that plugs into the
conversation experience. It knows nothing about Spanish, taxis, or
grammar. It knows how to run an ensemble of characters through their arcs,
one visit at a time.

## The cast

Four recurring characters, each with a four-beat arc that builds
investment (`story/cast.py`):

| Character | Role | Their story |
|---|---|---|
| **Carlos** | taxi driver | Saving for his daughter Sofía's quinceañera — dress drama, double shifts, the big day, the aftermath |
| **María** | hotel receptionist | Never left her city → secretly applies for a job abroad → the interview |
| **Ana** | coworker | A big presentation → a shot at promotion → what comes after |
| **Diego** | restaurant owner | A struggling restaurant, his late abuela's recipes, a critic's visit, the review |

Each **beat** is one scene: a `situation` (what Kai stages), a `hook` (the
tension that makes the learner lean in), a `reveal` (what deepens
investment), and a `memory` (what the learner will remember, for Kai to
reference next time).

## How a session uses it

1. **Start** — the conversation experience asks the engine who the learner
   meets tonight. The engine picks a character and stages their current
   beat, and that scene is frozen into the session (so it stays consistent
   even as the conversation runs).
2. **Play** — the scene goes into Kai's system prompt (`persona.scene_brief`).
   Kai stays the teacher but also voices the character and sets the scene,
   hiding today's target concepts inside it. He references prior encounters
   from the thread's stored memories — naturally, the way you'd remember a
   mutual friend, never as a recap.
3. **End** — the character's thread advances one beat and records the
   memory. Next time the learner returns to that character, the story
   continues from there. (A session with zero learner turns doesn't
   advance anything — the story waits for them.)

## How the ensemble grows

Selection is deterministic (`story/engine.py`), so a learner's story
unfolds the same way on replay:

- First ever session: they meet Carlos (the friendliest way in).
- The engine folds in a new character every few sessions until the whole
  cast is established (~six sessions), then rotates through whoever's been
  waiting longest — advancing each arc a beat at a time.

A real run looks like this:

```
Session 1: Carlos  [meets]     flags down his taxi at the airport
Session 2: María   [meets]     checks in, reservation snag
Session 3: Ana     [meets]     first day at the office
Session 4: Carlos  [back #2]   ↳ Kai recalls: saving for Sofía's quinceañera
Session 5: María   [back #2]   ↳ Kai recalls: she's never left this city
Session 6: Diego   [meets]     wanders into the half-empty restaurant
Session 7: Ana     [back #2]   ↳ Kai recalls: nervous about her presentation
Session 8: Carlos  [back #3]   ↳ Kai recalls: dress didn't fit, double shifts
Session 9: María   [back #3]   ↳ the printed email under the desk...
```

Carlos's quinceañera saga threads across sessions 1 → 4 → 8, each visit
carrying forward what happened before.

## Reusability

The engine sequences arcs; the cast supplies the world. A new subject
gets its own characters with one call:

```python
from story import register_cast, Character, Beat

register_cast("guitar", (
    Character(id="rosa", name="Rosa", role="open-mic host", persona="...",
              arc=(Beat("rosa-1", "...", "...", "...", "..."), ...),
              resting="Rosa waves you up for another song."),
    # ...
))
```

Nothing in `story/engine.py` changes. The selection logic, the memory
handling, the beat sequencing, the persistence — all of it is
content-agnostic.

## What lives where

| File | Responsibility |
|---|---|
| `story/cast.py` | Characters and their arcs (the content) |
| `story/state.py` | Per-learner story threads (progress + memories) |
| `story/engine.py` | Selection + beat sequencing (the reusable logic) |
| `story/store.py` | JSON persistence, one file per learner |
| `app/persona.py` | `scene_brief()` — renders the scene as direction for Kai |
| `app/conversation.py` | Stages the scene at start, advances the thread at end |
