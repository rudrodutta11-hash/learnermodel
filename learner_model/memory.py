"""Adaptive memory model: we learn each learner's forgetting curve.

Standard spaced-repetition systems assume a fixed forgetting curve and
schedule reviews around it. We do the opposite: the curve itself is a
per-learner parameter, estimated continuously from recall outcomes.

Each knowledge atom has a memory half-life. Retrievability decays as

    R(t) = 2 ** (-elapsed / half_life)

Successful recall multiplies the half-life (stability gain); failure
shrinks it. Critically, the *gain* and the *starting half-life* are
learner-level traits: a fast forgetter and a slow forgetter who study the
same item get different schedules from day one — and those traits follow
the learner into every new subject.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# Learner-trait bounds (hours / multipliers)
MIN_BASE_HALF_LIFE_H = 2.0
MAX_BASE_HALF_LIFE_H = 96.0
MIN_STABILITY_GAIN = 1.3
MAX_STABILITY_GAIN = 4.0


@dataclass
class ItemMemory:
    """Memory state for one knowledge atom for one learner."""

    item_key: str
    half_life_hours: float
    last_review_ts: float
    repetitions: int = 0
    lapses: int = 0

    def retrievability(self, now_ts: float) -> float:
        elapsed_h = max(0.0, (now_ts - self.last_review_ts) / 3600.0)
        return 2.0 ** (-elapsed_h / self.half_life_hours)


@dataclass
class MemoryModel:
    """Per-learner forgetting model, shared across all subjects."""

    base_half_life_hours: float = 24.0   # trait: how sticky is first exposure
    stability_gain: float = 2.0          # trait: how much each success compounds
    trait_learning_rate: float = 0.1
    items: dict[str, ItemMemory] = field(default_factory=dict)

    def observe(self, item_key: str, correct: bool, now_ts: float) -> None:
        """Update item state and, via prediction error, the learner traits."""
        item = self.items.get(item_key)
        if item is None:
            # First exposure: seed from the learner-level trait.
            self.items[item_key] = ItemMemory(
                item_key=item_key,
                half_life_hours=self.base_half_life_hours,
                last_review_ts=now_ts,
                repetitions=1,
                lapses=0 if correct else 1,
            )
            return

        predicted = item.retrievability(now_ts)
        actual = 1.0 if correct else 0.0
        surprise = actual - predicted  # + : remembers better than we thought

        # Update learner traits from prediction error. Recalling against the
        # odds means this person forgets slower than modeled, and vice versa.
        self.base_half_life_hours = _clamp(
            self.base_half_life_hours * math.exp(self.trait_learning_rate * surprise),
            MIN_BASE_HALF_LIFE_H,
            MAX_BASE_HALF_LIFE_H,
        )
        self.stability_gain = _clamp(
            self.stability_gain * math.exp(self.trait_learning_rate * 0.5 * surprise),
            MIN_STABILITY_GAIN,
            MAX_STABILITY_GAIN,
        )

        # Update the item itself.
        if correct:
            # Harder recalls (low predicted R) earn bigger stability boosts.
            boost = self.stability_gain * (1.0 + (1.0 - predicted))
            item.half_life_hours *= boost
            item.repetitions += 1
        else:
            # Shrink, but never below half the learner's base half-life:
            # a lapse means "relearn", not "never knew it". Over-shrinking
            # would make later predictions so pessimistic that every recall
            # looks like a positive surprise, biasing the traits upward.
            item.half_life_hours = max(
                self.base_half_life_hours * 0.5, item.half_life_hours * 0.6
            )
            item.lapses += 1
        item.last_review_ts = now_ts

    def retrievability(self, item_key: str, now_ts: float) -> float:
        item = self.items.get(item_key)
        return item.retrievability(now_ts) if item is not None else 0.0

    def items_at_risk(
        self, now_ts: float, threshold: float = 0.6, subject: str | None = None
    ) -> list[tuple[str, float]]:
        """Knowledge atoms about to be forgotten, weakest first."""
        prefix = f"{subject}:" if subject else ""
        at_risk = [
            (key, item.retrievability(now_ts))
            for key, item in self.items.items()
            if key.startswith(prefix) and item.retrievability(now_ts) < threshold
        ]
        return sorted(at_risk, key=lambda pair: pair[1])


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
