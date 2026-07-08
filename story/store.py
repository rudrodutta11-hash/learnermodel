"""JSON persistence for story state — one file per learner, same seam as
learner_model.store. Swap for a real DB behind these two functions when
there are users."""
from __future__ import annotations

import json
from pathlib import Path

from .state import CharacterThread, StoryState

SCHEMA_VERSION = 1


def save_story(state: StoryState, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{state.learner_id}.json"
    path.write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "learner_id": state.learner_id,
        "threads": {
            cid: {
                "character_id": t.character_id,
                "beat_index": t.beat_index,
                "encounters": t.encounters,
                "last_seen_ts": t.last_seen_ts,
                "memories": t.memories,
            }
            for cid, t in state.threads.items()
        },
    }, indent=2))
    return path


def load_story(learner_id: str, directory: Path) -> StoryState:
    path = directory / f"{learner_id}.json"
    if not path.exists():
        return StoryState(learner_id=learner_id)
    data = json.loads(path.read_text())
    return StoryState(
        learner_id=data["learner_id"],
        threads={
            cid: CharacterThread(**t) for cid, t in data.get("threads", {}).items()
        },
    )
