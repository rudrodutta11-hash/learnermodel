"""The Teacher Brain — the layer that DECIDES, above the Kai who TEACHES.

The journal is the teacher's memory: what happened. The Brain is the
teacher's foresight: what will happen, and whether he was right. It is the
next natural evolution of the journal — same source (the learner model),
opposite direction in time.

The loop, bracketing every session:

    predict(...)   at session start: read the learner model and emit typed
                   predictions — each with confidence and evidence, outcome
                   left blank. Kai teaches WITH these in front of him.
    resolve(...)   at session end: check each open prediction against what
                   actually happened. correct / wrong / not-yet-testable.
    calibration()  over resolved predictions: how often was the Brain right,
                   per kind, for THIS learner. That number is the seed of
                   getting better — and, eventually, what the recommendation
                   engine consumes to trust some predictions over others.

This is architecture, not machine learning. Generation and evaluation are
deterministic rules over the learner model. What "learns" is the running
record of which predictions hold for which learner — accumulated evidence,
ready for a smarter policy to sit on top later.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum

from learner_model.knowledge import KnowledgeState
from learner_model.profile import LearnerProfile

TARGET_SUCCESS = 0.8
# A prediction that can't be tested yet is carried this many more sessions
# before we give up and mark it unresolvable (excluded from the hit-rate).
MAX_CARRY_SESSIONS = 2


class PredictionKind(str, Enum):
    READY_FOR_CHALLENGE = "ready_for_challenge"
    NEEDS_CONFIDENCE_FIRST = "needs_confidence_first"
    RETAINS_BETTER_VIA = "retains_better_via"
    FRUSTRATION_RISK = "frustration_risk"
    NEAR_BREAKTHROUGH = "near_breakthrough"


@dataclass
class SessionOutcome:
    """What actually happened in a session — the evidence resolution uses."""

    engaged: bool
    success: float                 # fraction of practiced concepts gotten right, 0..1
    difficulty: float
    modality: str
    concepts: tuple[str, ...] = ()
    mistakes: tuple[str, ...] = () # error signatures / missed concept ids
    confidence: float | None = None


@dataclass
class Prediction:
    """One forecast about the learner, with its outcome filled in later."""

    kind: PredictionKind
    statement: str                 # what the Brain expects, in plain words
    confidence: float              # how sure the Brain is, 0..1
    evidence: str                  # why it believes this — from the learner model
    subject_ref: str = ""          # optional: the modality / topic / concept it's about
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    # --- outcome, filled by resolve() ---
    resolved: bool = False
    correct: bool | None = None    # True / False, or None = untestable
    outcome_note: str = ""
    resolved_at: float | None = None
    sessions_carried: int = 0      # how many session-ends have passed unresolved

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Prediction":
        d = dict(d)
        d["kind"] = PredictionKind(d["kind"])
        return cls(**d)


class TeacherBrain:
    # ------------------------------------------------------------------ predict

    def predict(
        self, profile: LearnerProfile, knowledge: KnowledgeState, now: float
    ) -> list[Prediction]:
        """Read the learner model and forecast this session. Deterministic:
        the same learner state always yields the same reads, so predictions
        are reproducible and testable."""
        ch = profile.challenge
        preds: list[Prediction] = []

        # Ready for more challenge vs. needs confidence first — mutually
        # exclusive reads of the same success/tolerance signals.
        if ch.success_rate >= 0.82 and ch.frustration_tolerance >= 0.4:
            margin = ch.success_rate - TARGET_SUCCESS
            preds.append(Prediction(
                kind=PredictionKind.READY_FOR_CHALLENGE,
                statement="Ready for more challenge — push the difficulty up a notch.",
                confidence=_clamp(0.55 + margin * 2.0),
                evidence=(f"Succeeding around {ch.success_rate:.0%} and stays with it "
                          f"when it gets hard (tolerance {ch.frustration_tolerance:.0%})."),
            ))
        elif ch.success_rate < 0.6 or profile.confidence.is_underconfident:
            preds.append(Prediction(
                kind=PredictionKind.NEEDS_CONFIDENCE_FIRST,
                statement="Needs a confidence win before any real difficulty.",
                confidence=_clamp(0.6 + (0.6 - ch.success_rate)),
                evidence=(f"Success around {ch.success_rate:.0%}"
                          + (", and self-doubt runs ahead of ability."
                             if profile.confidence.is_underconfident else ".")),
            ))

        # Which format sticks — only when one clearly leads.
        ranked = profile.modality.ranked()
        if len(ranked) >= 2 and ranked[0][1] - ranked[1][1] >= 0.2:
            top, gap = ranked[0][0], ranked[0][1] - ranked[1][1]
            preds.append(Prediction(
                kind=PredictionKind.RETAINS_BETTER_VIA,
                statement=f"Will retain more through {top.value.replace('_', ' ')} tonight.",
                confidence=_clamp(0.5 + gap),
                evidence=f"{top.value.replace('_', ' ')} is outperforming everything else "
                         f"for them by {gap:.0%}.",
                subject_ref=top.value,
            ))

        # Frustration risk — a mistake they keep hitting, and low tolerance.
        recurring = profile.errors.recurring(now, subject=knowledge.subject)
        if recurring and ch.frustration_tolerance < 0.45:
            top = recurring[0]
            preds.append(Prediction(
                kind=PredictionKind.FRUSTRATION_RISK,
                statement=f"Likely to get frustrated by {top.signature.replace('-', ' ')} today.",
                confidence=_clamp(0.5 + 0.1 * top.count),
                evidence=(f"Hit {top.signature.replace('-', ' ')} {top.count} times and "
                          f"doesn't have much patience for struggle right now."),
                subject_ref=top.signature,
            ))

        # Near a breakthrough — a stubborn error whose recurrence is decaying
        # (they're stopping making it). May not resolve until next session.
        if recurring:
            top = recurring[0]
            severity = top.severity(now)
            if top.count >= 3 and severity < top.count * 0.5:  # count high, recency fading
                preds.append(Prediction(
                    kind=PredictionKind.NEAR_BREAKTHROUGH,
                    statement=f"Close to a breakthrough on {top.signature.replace('-', ' ')}.",
                    confidence=0.55,
                    evidence=(f"Used to hit {top.signature.replace('-', ' ')} constantly; "
                              f"it's been fading. One clean session could lock it in."),
                    subject_ref=top.signature,
                ))
        return preds

    # ------------------------------------------------------------------ resolve

    def resolve(self, prediction: Prediction, outcome: SessionOutcome,
                now: float) -> Prediction:
        """Check one open prediction against what happened. Sets correct to
        True/False when the session gives a clear answer, or leaves it open
        (correct stays None, sessions_carried grows) when it can't yet tell —
        until MAX_CARRY_SESSIONS, after which it's marked unresolvable."""
        verdict, note = self._judge(prediction, outcome)

        if verdict is None:
            prediction.sessions_carried += 1
            if prediction.sessions_carried > MAX_CARRY_SESSIONS:
                prediction.resolved = True
                prediction.correct = None
                prediction.outcome_note = "Never became testable — set aside."
                prediction.resolved_at = now
            return prediction

        prediction.resolved = True
        prediction.correct = verdict
        prediction.outcome_note = note
        prediction.resolved_at = now
        return prediction

    def _judge(self, p: Prediction, o: SessionOutcome) -> tuple[bool | None, str]:
        touched = p.subject_ref in o.concepts or p.subject_ref in o.mistakes
        missed_ref = p.subject_ref in o.mistakes

        if p.kind is PredictionKind.READY_FOR_CHALLENGE:
            if not o.engaged:
                return False, "Pushed them and they checked out — not ready."
            if o.success >= 0.6:
                return True, f"Handled it: engaged, {o.success:.0%} right."
            return False, f"Struggled harder than expected ({o.success:.0%} right)."

        if p.kind is PredictionKind.NEEDS_CONFIDENCE_FIRST:
            # Right if the session was indeed fragile (low success or disengaged).
            if not o.engaged or o.success < 0.7:
                return True, "Confirmed — still shaky, right call to protect confidence."
            return False, "Actually steadier than expected; could have pushed."

        if p.kind is PredictionKind.RETAINS_BETTER_VIA:
            if o.modality != p.subject_ref:
                return None, "Different format tonight — couldn't test the claim."
            if o.engaged and o.success >= 0.6:
                return True, f"{p.subject_ref.replace('_', ' ')} landed as predicted."
            return False, f"{p.subject_ref.replace('_', ' ')} didn't click this time."

        if p.kind is PredictionKind.FRUSTRATION_RISK:
            if not touched:
                return None, "The rough topic never came up — no test."
            if missed_ref or not o.engaged or (o.confidence is not None and o.confidence < 0.4):
                return True, "It did trip them up, as feared."
            return False, "Faced it and stayed fine — better than predicted."

        if p.kind is PredictionKind.NEAR_BREAKTHROUGH:
            if not touched:
                return None, "Didn't revisit it — the breakthrough's still pending."
            if not missed_ref:
                return True, "Got it clean — breakthrough landed."
            return False, "Still slipping on it; not yet."

        return None, ""

    # -------------------------------------------------------------- calibration

    def calibration(self, resolved: list[Prediction]) -> dict:
        """How often the Brain has been right, per kind and overall, for this
        learner. Only decisive outcomes (correct True/False) count — untestable
        ones are excluded. This is the accuracy that grows over time and that
        a later policy (or the recommendation engine) can trust."""
        by_kind: dict[str, dict] = {}
        hits = total = 0
        for p in resolved:
            if p.correct is None:
                continue
            k = by_kind.setdefault(p.kind.value, {"hits": 0, "total": 0})
            k["total"] += 1
            total += 1
            if p.correct:
                k["hits"] += 1
                hits += 1
        for k in by_kind.values():
            k["accuracy"] = round(k["hits"] / k["total"], 3) if k["total"] else None
        return {
            "overall_accuracy": round(hits / total, 3) if total else None,
            "decided": total,
            "by_kind": by_kind,
        }


def _clamp(x: float, lo: float = 0.5, hi: float = 0.95) -> float:
    return max(lo, min(hi, x))
