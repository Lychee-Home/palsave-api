"""Persisted watcher state: last processed backup folder, last archived
snapshot path, next event ID, and the append-only new-pal event log itself.
Read fresh from disk by both the watcher (after every tick) and the API
(on every request), so a restart of either always sees the latest committed
state.
"""

import json
from pathlib import Path

DEFAULT_STATE = {
    "last_processed": None,
    "last_snapshot_path": None,
    "next_event_id": 1,
    "events": [],
}


def load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return dict(DEFAULT_STATE, events=[])
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(state_path: Path, state: dict) -> None:
    state_path.write_text(json.dumps(state), encoding="utf-8")


def append_events(state: dict, new_events: list) -> None:
    """Assign each event the next monotonically increasing ID and append it
    to the log in place, mutating `state`."""
    for event in new_events:
        event["id"] = state["next_event_id"]
        state["next_event_id"] += 1
        state["events"].append(event)


def query_events(state: dict, since: int, limit: int) -> list:
    matching = [e for e in state["events"] if e["id"] > since]
    return matching[:limit]
