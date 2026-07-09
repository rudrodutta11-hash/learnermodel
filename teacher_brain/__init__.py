"""The Teacher Brain: predicts what will work for a learner, then checks
whether it was right — the foresight layer above Kai's teaching."""
from .brain import (
    Prediction,
    PredictionKind,
    SessionOutcome,
    TeacherBrain,
)

__all__ = ["Prediction", "PredictionKind", "SessionOutcome", "TeacherBrain"]
