"""Recurring mistake tracking.

Subjects tag failed interactions with a canonical `error_signature`
(e.g. "ser-vs-estar", "sign-error-in-derivative", "confuses-1848-1849").
The learner model doesn't understand the tags — it just detects
recurrence and decay, so the recommender can target drills at mistakes
the learner keeps making rather than ones they made once and fixed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

RECENCY_HALF_LIFE_H = 7 * 24.0  # a week-old mistake counts half


@dataclass
class ErrorRecord:
    signature: str
    subject: str
    count: int = 0
    last_seen_ts: float = 0.0

    def recency_weight(self, now_ts: float) -> float:
        elapsed_h = max(0.0, (now_ts - self.last_seen_ts) / 3600.0)
        return 2.0 ** (-elapsed_h / RECENCY_HALF_LIFE_H)

    def severity(self, now_ts: float) -> float:
        """Recurrence x recency: the number the recommender sorts by."""
        return math.log1p(self.count) * self.recency_weight(now_ts)


@dataclass
class ErrorModel:
    records: dict[str, ErrorRecord] = field(default_factory=dict)

    def observe(self, signature: str | None, subject: str, now_ts: float) -> None:
        if not signature:
            return
        key = f"{subject}:{signature}"
        record = self.records.setdefault(
            key, ErrorRecord(signature=signature, subject=subject)
        )
        record.count += 1
        record.last_seen_ts = now_ts

    def recurring(
        self, now_ts: float, subject: str | None = None, min_count: int = 2
    ) -> list[ErrorRecord]:
        """Mistakes worth targeting, most severe first."""
        found = [
            r
            for r in self.records.values()
            if r.count >= min_count and (subject is None or r.subject == subject)
        ]
        return sorted(found, key=lambda r: r.severity(now_ts), reverse=True)
