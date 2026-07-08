"""The challenge dial: how much difficulty keeps THIS learner engaged.

Flow theory says learning peaks when success is likely but not certain.
The classic target is ~80-85% success, but the right operating point is
personal: some learners thrive on struggle, others churn the moment they
feel stupid. We track the relationship between difficulty, success, and
engagement, and expose the difficulty the recommender should aim for next.
"""
from __future__ import annotations

from dataclasses import dataclass

TARGET_SUCCESS_RATE = 0.8


@dataclass
class ChallengeModel:
    """Estimates the difficulty sweet spot for one learner."""

    comfort_difficulty: float = 0.5   # difficulty at which learner succeeds ~target rate
    success_rate: float = 0.7         # EMA of recent correctness
    frustration_tolerance: float = 0.5  # engagement while failing → tolerance for struggle
    smoothing: float = 0.15

    def observe(self, difficulty: float, correct: bool | None, engaged: bool) -> None:
        if correct is not None:
            actual = 1.0 if correct else 0.0
            self.success_rate = (
                (1.0 - self.smoothing) * self.success_rate + self.smoothing * actual
            )
            # If they're succeeding above target, the comfort zone is higher
            # than we thought; below target, lower.
            direction = 1.0 if self.success_rate > TARGET_SUCCESS_RATE else -1.0
            self.comfort_difficulty = _clamp01(
                self.comfort_difficulty + direction * self.smoothing * 0.2
            )
            if not correct:
                tolerance_signal = 1.0 if engaged else 0.0
                self.frustration_tolerance = (
                    (1.0 - self.smoothing) * self.frustration_tolerance
                    + self.smoothing * tolerance_signal
                )

    def target_difficulty(self) -> float:
        """Where the recommender should aim: comfort zone plus a stretch
        proportional to how well this learner handles failing."""
        stretch = 0.15 * self.frustration_tolerance
        return _clamp01(self.comfort_difficulty + stretch)

    def fit(self, activity_difficulty: float) -> float:
        """Score in [0, 1] for how well a difficulty suits this learner."""
        distance = abs(activity_difficulty - self.target_difficulty())
        return max(0.0, 1.0 - 2.0 * distance)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))
