"""Which teaching methods actually work for THIS learner.

Every modality (story, quiz, flashcard, conversation, ...) is treated as an
arm of a multi-armed bandit. Each arm holds a Beta posterior over "an
interaction through this modality succeeds for this learner". Thompson
sampling gives us exploration for free: early on the model tries
everything; over time it concentrates on what demonstrably works — while
never fully abandoning the rest, because people change.

This is a learner-level model. Modality effectiveness learned in Spanish
transfers to History on day one.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .events import Modality


@dataclass
class ModalityStats:
    alpha: float = 1.0  # successes + 1 (uniform prior)
    beta: float = 1.0   # failures + 1

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def observations(self) -> float:
        return self.alpha + self.beta - 2.0

    def sample(self, rng: random.Random) -> float:
        return rng.betavariate(self.alpha, self.beta)


@dataclass
class ModalityModel:
    """Beta-Bernoulli bandit over teaching modalities for one learner."""

    stats: dict[Modality, ModalityStats] = field(
        default_factory=lambda: {m: ModalityStats() for m in Modality}
    )
    rng: random.Random = field(default_factory=random.Random)

    def observe(self, modality: Modality, success: bool, engaged: bool) -> None:
        s = self.stats.setdefault(modality, ModalityStats())
        # Disengagement is a failure of the teaching method even if the
        # answer happened to be right.
        effective_success = success and engaged
        if effective_success:
            s.alpha += 1.0
        else:
            s.beta += 1.0

    def effectiveness(self, modality: Modality) -> float:
        """Posterior mean — use for reporting and stable scoring."""
        return self.stats.setdefault(modality, ModalityStats()).mean

    def explore_score(self, modality: Modality) -> float:
        """Thompson sample — use inside the recommender for explore/exploit."""
        return self.stats.setdefault(modality, ModalityStats()).sample(self.rng)

    def ranked(self) -> list[tuple[Modality, float]]:
        return sorted(
            ((m, s.mean) for m, s in self.stats.items()),
            key=lambda pair: pair[1],
            reverse=True,
        )

    def best(self) -> Modality:
        return self.ranked()[0][0]
