# tests/test_config.py
import importlib
import os
import unittest
from pathlib import Path


class TestConfig(unittest.TestCase):
    def setUp(self):
        self._old_backup_dir = os.environ.get("PALSAVE_API_BACKUP_DIR")
        self._old_port = os.environ.get("PALSAVE_API_PORT")

    def tearDown(self):
        if self._old_backup_dir is None:
            os.environ.pop("PALSAVE_API_BACKUP_DIR", None)
        else:
            os.environ["PALSAVE_API_BACKUP_DIR"] = self._old_backup_dir
        if self._old_port is None:
            os.environ.pop("PALSAVE_API_PORT", None)
        else:
            os.environ["PALSAVE_API_PORT"] = self._old_port

    def test_reads_backup_dir_and_default_port(self):
        os.environ["PALSAVE_API_BACKUP_DIR"] = "/tmp/backups"
        os.environ.pop("PALSAVE_API_PORT", None)
        import config
        importlib.reload(config)
        self.assertEqual(config.BACKUP_DIR, Path("/tmp/backups"))
        self.assertEqual(config.PORT, 8787)
        self.assertEqual(config.ARCHIVE_DIR, Path("snapshots"))
        self.assertEqual(config.STATE_PATH, Path("state.json"))

    def test_port_override(self):
        os.environ["PALSAVE_API_BACKUP_DIR"] = "/tmp/backups"
        os.environ["PALSAVE_API_PORT"] = "9999"
        import config
        importlib.reload(config)
        self.assertEqual(config.PORT, 9999)


if __name__ == "__main__":
    unittest.main()
