"""JSON persistence for learner profiles.

Deliberately boring: one JSON file per learner. This is a seam, not a
commitment — swap in SQLite/Postgres behind the same two functions when
we have users. The serialization format IS worth getting right early,
because profiles are long-lived assets that must survive schema drift.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from .challenge import ChallengeModel
from .confidence import ConfidenceModel
from .errors import ErrorModel, ErrorRecord
from .events import Modality
from .focus import FocusModel
from .memory import ItemMemory, MemoryModel
from .modality import ModalityModel, ModalityStats
from .profile import LearnerProfile

SCHEMA_VERSION = 1


def save_profile(profile: LearnerProfile, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{profile.learner_id}.json"
    path.write_text(json.dumps(_to_dict(profile), indent=2))
    return path


def load_profile(learner_id: str, directory: Path) -> LearnerProfile:
    path = directory / f"{learner_id}.json"
    return _from_dict(json.loads(path.read_text()))


def _to_dict(p: LearnerProfile) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "learner_id": p.learner_id,
        "total_events": p.total_events,
        "subjects_seen": sorted(p.subjects_seen),
        "memory": {
            "base_half_life_hours": p.memory.base_half_life_hours,
            "stability_gain": p.memory.stability_gain,
            "items": {
                k: {
                    "half_life_hours": i.half_life_hours,
                    "last_review_ts": i.last_review_ts,
                    "repetitions": i.repetitions,
                    "lapses": i.lapses,
                }
                for k, i in p.memory.items.items()
            },
        },
        "modality": {
            m.value: {"alpha": s.alpha, "beta": s.beta} for m, s in p.modality.stats.items()
        },
        "focus": {"attention_span_minutes": p.focus.attention_span_minutes},
        "confidence": {
            "calibration_bias": p.confidence.calibration_bias,
            "brier_score": p.confidence.brier_score,
            "observations": p.confidence.observations,
        },
        "errors": {
            k: {
                "signature": r.signature,
                "subject": r.subject,
                "count": r.count,
                "last_seen_ts": r.last_seen_ts,
            }
            for k, r in p.errors.records.items()
        },
        "challenge": {
            "comfort_difficulty": p.challenge.comfort_difficulty,
            "success_rate": p.challenge.success_rate,
            "frustration_tolerance": p.challenge.frustration_tolerance,
        },
    }


def _from_dict(d: dict) -> LearnerProfile:
    memory = MemoryModel(
        base_half_life_hours=d["memory"]["base_half_life_hours"],
        stability_gain=d["memory"]["stability_gain"],
        items={
            k: ItemMemory(item_key=k, **v) for k, v in d["memory"]["items"].items()
        },
    )
    modality = ModalityModel(
        stats={Modality(m): ModalityStats(**s) for m, s in d["modality"].items()},
        rng=random.Random(),
    )
    focus = FocusModel(attention_span_minutes=d["focus"]["attention_span_minutes"])
    confidence = ConfidenceModel(**d["confidence"])
    errors = ErrorModel(records={k: ErrorRecord(**v) for k, v in d["errors"].items()})
    challenge = ChallengeModel(**d["challenge"])
    return LearnerProfile(
        learner_id=d["learner_id"],
        memory=memory,
        modality=modality,
        focus=focus,
        confidence=confidence,
        errors=errors,
        challenge=challenge,
        total_events=d["total_events"],
        subjects_seen=set(d["subjects_seen"]),
    )
