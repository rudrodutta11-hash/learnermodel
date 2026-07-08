"""Confidence calibration: does this learner know what they know?

We compare self-reported confidence against actual correctness.
Persistent overconfidence means the learner needs more retrieval practice
(testing reveals illusions of knowing). Persistent underconfidence means
they need engineered wins to build trust in their own knowledge. The
recommender consumes the calibration bias directly.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConfidenceModel:
    """Running calibration estimate for one learner (subject-agnostic)."""

    calibration_bias: float = 0.0   # + overconfident, - underconfident
    brier_score: float = 0.25       # mean squared confidence error (lower = better)
    observations: int = 0
    smoothing: float = 0.1

    def observe(self, confidence: float | None, correct: bool | None) -> None:
        if confidence is None or correct is None:
            return
        actual = 1.0 if correct else 0.0
        error = confidence - actual
        self.calibration_bias = (
            (1.0 - self.smoothing) * self.calibration_bias + self.smoothing * error
        )
        self.brier_score = (
            (1.0 - self.smoothing) * self.brier_score + self.smoothing * error * error
        )
        self.observations += 1

    @property
    def is_overconfident(self) -> bool:
        return self.observations >= 5 and self.calibration_bias > 0.15

    @property
    def is_underconfident(self) -> bool:
        return self.observations >= 5 and self.calibration_bias < -0.15
