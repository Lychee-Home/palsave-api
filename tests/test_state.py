import json
import tempfile
import unittest
from pathlib import Path

from state import append_events, load_state, query_events, save_state


class TestState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_missing_file_returns_default(self):
        state = load_state(self.state_path)
        self.assertIsNone(state["last_processed"])
        self.assertIsNone(state["last_snapshot_path"])
        self.assertEqual(state["next_event_id"], 1)
        self.assertEqual(state["events"], [])

    def test_default_state_events_list_not_shared_across_calls(self):
        a = load_state(self.state_path)
        a["events"].append({"id": 1})
        b = load_state(self.state_path)
        self.assertEqual(b["events"], [])

    def test_save_and_load_round_trip(self):
        state = {"last_processed": "folder1", "last_snapshot_path": "snapshots/folder1.sav",
                  "next_event_id": 3, "events": [{"id": 1, "character_id": "Lamball"}]}
        save_state(self.state_path, state)
        loaded = load_state(self.state_path)
        self.assertEqual(loaded, state)

    def test_corrupt_file_raises(self):
        self.state_path.write_text("not json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            load_state(self.state_path)

    def test_append_events_assigns_increasing_ids(self):
        state = {"last_processed": None, "last_snapshot_path": None, "next_event_id": 5, "events": []}
        append_events(state, [{"character_id": "A"}, {"character_id": "B"}])
        self.assertEqual(state["events"][0]["id"], 5)
        self.assertEqual(state["events"][1]["id"], 6)
        self.assertEqual(state["next_event_id"], 7)

    def test_query_events_filters_since(self):
        state = {"events": [{"id": 1}, {"id": 2}, {"id": 3}]}
        self.assertEqual(query_events(state, since=1, limit=10), [{"id": 2}, {"id": 3}])

    def test_query_events_respects_limit(self):
        state = {"events": [{"id": 1}, {"id": 2}, {"id": 3}]}
        self.assertEqual(query_events(state, since=0, limit=2), [{"id": 1}, {"id": 2}])

    def test_query_events_since_beyond_max_is_empty(self):
        state = {"events": [{"id": 1}, {"id": 2}]}
        self.assertEqual(query_events(state, since=99, limit=10), [])


if __name__ == "__main__":
    unittest.main()
