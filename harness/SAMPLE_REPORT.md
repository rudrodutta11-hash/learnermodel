> **SAMPLE OUTPUT.** This is the harness rendered over the synthetic
> `--self-test` transcript, to show the report shape. A real run
> (`python -m harness.validate_conversation` with an API key) replaces
> the transcript below with live model output and real token costs.

# Kai conversation validation — beginner_spanish_returning (SAMPLE — synthetic, not a real run)
- model: `claude-haiku-4-5`  ·  budget: 5 min  ·  run: 2026-07-10T00:00:00+00:00
- tokens: 4120 in / 610 out  ·  est. cost: $0.00718

## Rule report

| rule | result | kind | detail |
|---|---|---|---|
| banned_language | PASS | exact | clean — no syllabus words or chatbot tells |
| producing_early | PASS | heuristic | opener ends on a question that asks for output |
| echo_correction | PASS | exact | echoed the fix 'tengo' |
| turn_length_1_to_3 | PASS | heuristic | all turns ≤ 3 sentences |
| ending_names_specific | PASS | heuristic | close affirms + references today's material |

**5/5 rules passed.**

## Transcript

**KAI** [opening · 0.0m]

Back again — good. Say hello to Carlos in Spanish. What do you tell him?

**LEARNER** [mid · 2.2m]

yo tiene hambre

**KAI** [mid · 2.2m]

Almost — yo tengo, not tiene. Ownership is yours. Say it again?

**LEARNER** [closing · 4.8m]

yo tengo hambre!

**KAI** [closing · 4.8m]

There it is — you fixed tener cleanly and fast. That's the win today.
