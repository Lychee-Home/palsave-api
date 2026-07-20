import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("PALSAVE_API_BACKUP_DIR", tempfile.mkdtemp())

from fastapi.testclient import TestClient

import config
from state import save_state


class TestEventsEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_backup_dir = config.BACKUP_DIR
        self._old_archive_dir = config.ARCHIVE_DIR
        self._old_state_path = config.STATE_PATH
        config.BACKUP_DIR = Path(self.tmp.name) / "backups"
        config.BACKUP_DIR.mkdir()
        config.ARCHIVE_DIR = Path(self.tmp.name) / "snapshots"
        config.STATE_PATH = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        config.BACKUP_DIR = self._old_backup_dir
        config.ARCHIVE_DIR = self._old_archive_dir
        config.STATE_PATH = self._old_state_path
        self.tmp.cleanup()

    def test_returns_events_after_since_up_to_limit(self):
        save_state(config.STATE_PATH, {
            "last_processed": "f", "last_snapshot_path": "s", "next_event_id": 4,
            "events": [{"id": 1, "character_id": "A"}, {"id": 2, "character_id": "B"},
                       {"id": 3, "character_id": "C"}],
        })
        import api
        with TestClient(api.app) as client:
            resp = client.get("/events/new-pals", params={"since": 1, "limit": 1})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [{"id": 2, "character_id": "B"}])

    def test_defaults_since_zero_limit_twenty(self):
        events = [{"id": i} for i in range(1, 25)]
        save_state(config.STATE_PATH, {
            "last_processed": "f", "last_snapshot_path": "s", "next_event_id": 25, "events": events,
        })
        import api
        with TestClient(api.app) as client:
            resp = client.get("/events/new-pals")
        self.assertEqual(len(resp.json()), 20)
        self.assertEqual(resp.json()[0]["id"], 1)

    def test_missing_state_file_returns_empty_list(self):
        import api
        with TestClient(api.app) as client:
            resp = client.get("/events/new-pals")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_negative_limit_returns_empty_not_negative_slice(self):
        save_state(config.STATE_PATH, {
            "last_processed": "f", "last_snapshot_path": "s", "next_event_id": 4,
            "events": [{"id": 1, "character_id": "A"}, {"id": 2, "character_id": "B"},
                       {"id": 3, "character_id": "C"}],
        })
        import api
        with TestClient(api.app) as client:
            resp = client.get("/events/new-pals", params={"since": 0, "limit": -1})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_corrupt_state_file_returns_500(self):
        config.STATE_PATH.write_text("not json", encoding="utf-8")
        import api
        with TestClient(api.app) as client:
            resp = client.get("/events/new-pals")
        self.assertEqual(resp.status_code, 500)


if __name__ == "__main__":
    unittest.main()
