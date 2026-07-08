"""How long can this learner actually focus?

We estimate attention span from behavior, not self-report: within each
session we watch for the point where engagement drops (the learner bails,
idles, or starts failing items they know). The recommender uses this to
size activities — never handing a 25-minute story to a learner whose
demonstrated focus window is 8 minutes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .events import InteractionEvent


@dataclass
class FocusModel:
    """EMA estimate of a learner's productive focus window, in minutes."""

    attention_span_minutes: float = 15.0  # prior; adapts quickly
    smoothing: float = 0.25
    _session_id: str = field(default="", repr=False)
    _session_start_ts: float = field(default=0.0, repr=False)
    _minutes_engaged: float = field(default=0.0, repr=False)

    def observe(self, event: InteractionEvent) -> None:
        if event.session_id != self._session_id:
            self._finalize_session()
            self._session_id = event.session_id
            self._session_start_ts = event.timestamp
            self._minutes_engaged = 0.0

        if event.engaged:
            self._minutes_engaged += event.duration_seconds / 60.0
        else:
            # Disengagement marks the end of the productive window.
            self._update_span(self._minutes_engaged)
            self._minutes_engaged = 0.0
            self._session_start_ts = event.timestamp

    def end_session(self) -> None:
        """Call when a session ends without a disengagement signal."""
        self._finalize_session()
        self._session_id = ""

    def recommended_activity_minutes(self, available_minutes: float) -> float:
        """The activity length we should aim for right now."""
        return max(1.0, min(available_minutes, self.attention_span_minutes))

    def _finalize_session(self) -> None:
        # A session that ended while still engaged is a lower bound on span:
        # only pull the estimate up, never down.
        if self._minutes_engaged > self.attention_span_minutes:
            self._update_span(self._minutes_engaged)

    def _update_span(self, observed_minutes: float) -> None:
        if observed_minutes <= 0:
            return
        self.attention_span_minutes = (
            (1.0 - self.smoothing) * self.attention_span_minutes
            + self.smoothing * observed_minutes
        )
