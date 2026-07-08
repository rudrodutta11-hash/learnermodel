"""Session History: the append-only record of every learning interaction.

This is the system of record. LearnerProfile and KnowledgeState are both
*derived* state — if we ever improve the models, we can replay history and
rebuild every profile with the better math. Never mutate, never delete.

Storage is JSONL per learner: trivially portable to an event stream
(Kafka/Kinesis) or warehouse later without changing the write path's shape.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

from .events import InteractionEvent, Modality


class SessionHistory:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def append(self, event: InteractionEvent) -> None:
        record = asdict(event)
        record["modality"] = event.modality.value
        with self._path(event.learner_id).open("a") as f:
            f.write(json.dumps(record) + "\n")

    def events(self, learner_id: str) -> Iterator[InteractionEvent]:
        path = self._path(learner_id)
        if not path.exists():
            return
        with path.open() as f:
            for line in f:
                record = json.loads(line)
                record["modality"] = Modality(record["modality"])
                yield InteractionEvent(**record)

    def replay_into(self, profile) -> None:
        """Rebuild a profile from raw history — the derived-state guarantee."""
        for event in self.events(profile.learner_id):
            profile.update(event)

    def _path(self, learner_id: str) -> Path:
        return self.directory / f"{learner_id}.jsonl"
