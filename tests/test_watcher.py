import datetime
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

import watcher
from state import load_state
from watcher import list_backup_folders, process_new_backups


def build_palsav(raw: bytes) -> bytes:
    body = zlib.compress(raw)
    header = struct.pack("<II", len(raw), len(body)) + b"PlZ" + struct.pack("<B", 0x31)
    return header + body


def fstring(s: str) -> bytes:
    if s == "":
        return struct.pack("<i", 0)
    raw = s.encode("utf-8") + b"\x00"
    return struct.pack("<i", len(raw)) + raw


def guid_bytes(hex32: str) -> bytes:
    # hex32 e.g. "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" -> raw little-endian GUID bytes
    parts = hex32.split("-")
    a = struct.pack("<I", int(parts[0], 16))
    b = struct.pack("<H", int(parts[1], 16))
    c = struct.pack("<H", int(parts[2], 16))
    d = bytes.fromhex(parts[3] + parts[4])
    return a + b + c + d


def struct_property(name: str, struct_name: str, body: bytes) -> bytes:
    return (
        fstring(name) + fstring("StructProperty") + struct.pack("<q", len(body))
        + fstring(struct_name) + (b"\x00" * 16) + struct.pack("<B", 0) + body
    )


def guid_property_value(instance_id_hex: str) -> bytes:
    # A CharacterSaveParameterMap key struct: a small tagged property list
    # containing a single "InstanceId" Guid field, so gvas.py's
    # read_first_struct_scalar resolves it in "proplist" mode (matching
    # diff.py's expectation of entry["key"]["InstanceId"]), not the bare-Guid
    # fallback mode.
    return struct_property("InstanceId", "Guid", guid_bytes(instance_id_hex)) + fstring("None")


def map_property_one_entry(key_guid_hex: str, value_props: bytes) -> bytes:
    value_body = value_props + fstring("None")
    body = (
        struct.pack("<I", 0)  # removed count
        + struct.pack("<I", 1)  # entry count
        + guid_property_value(key_guid_hex)  # key: bare Guid (16 bytes)
        + value_body  # value: property list (RawData etc.), terminated by None
    )
    return (
        fstring("CharacterSaveParameterMap") + fstring("MapProperty") + struct.pack("<q", len(body))
        + fstring("StructProperty") + fstring("StructProperty") + struct.pack("<B", 0) + body
    )


def raw_data_property(inner_props: bytes) -> bytes:
    # ArrayProperty<ByteProperty> whose bytes decode as one embedded tagged
    # property (a StructProperty named "RawData" wrapping inner_props).
    embedded = struct_property("RawData", "PalIndividualCharacterSaveParameter", inner_props + fstring("None"))
    body = struct.pack("<I", len(embedded)) + embedded
    return (
        fstring("RawData") + fstring("ArrayProperty") + struct.pack("<q", len(body))
        + fstring("ByteProperty") + struct.pack("<B", 0) + body
    )


def build_world_save_data(entries_bytes: bytes) -> bytes:
    body = entries_bytes + fstring("None")
    return (
        fstring("worldSaveData") + fstring("StructProperty") + struct.pack("<q", len(body))
        + fstring("None") + (b"\x00" * 16) + struct.pack("<B", 0) + body
    )


def build_gvas_with_pal(instance_id_hex: str, owner_hex: str, level) -> bytes:
    level_prop = b""
    if level is not None:
        level_prop = (
            fstring("Level") + fstring("IntProperty") + struct.pack("<q", 4)
            + struct.pack("<B", 0) + struct.pack("<i", level)
        )
    owner_prop = struct_property("OwnerPlayerUId", "Guid", guid_bytes(owner_hex))
    inner = level_prop + owner_prop
    csm_entry = map_property_one_entry(instance_id_hex, raw_data_property(inner))
    wsd = build_world_save_data(csm_entry)

    header = (
        b"GVAS"
        + struct.pack("<iii", 1, 2, 3)
        + struct.pack("<HHH", 5, 1, 0)
        + struct.pack("<I", 0)
        + fstring("release")
        + struct.pack("<i", 0)
        + struct.pack("<I", 0)
        + fstring("SaveGameClass")
    )
    return header + wsd + fstring("None")


def write_backup_folder(root: Path, name: str, gvas_bytes: bytes):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "Level.sav").write_bytes(build_palsav(gvas_bytes))


def _snapshot_name(age: datetime.timedelta) -> str:
    when = datetime.datetime.now() - age
    return when.strftime(watcher.FOLDER_NAME_FORMAT) + ".sav"


class TestPruneSnapshots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive_dir = Path(self.tmp.name) / "snapshots"
        self.archive_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_deletes_old_snapshot(self):
        old = self.archive_dir / _snapshot_name(datetime.timedelta(hours=2))
        old.write_bytes(b"x")
        watcher.prune_snapshots(self.archive_dir, None)
        self.assertFalse(old.exists())

    def test_keeps_recent_snapshot(self):
        recent = self.archive_dir / _snapshot_name(datetime.timedelta(minutes=10))
        recent.write_bytes(b"x")
        watcher.prune_snapshots(self.archive_dir, None)
        self.assertTrue(recent.exists())

    def test_keeps_protected_snapshot_despite_age(self):
        old = self.archive_dir / _snapshot_name(datetime.timedelta(hours=2))
        old.write_bytes(b"x")
        watcher.prune_snapshots(self.archive_dir, str(old))
        self.assertTrue(old.exists())

    def test_ignores_non_matching_filenames(self):
        weird = self.archive_dir / "not-a-timestamp.sav"
        weird.write_bytes(b"x")
        readme = self.archive_dir / "README.txt"
        readme.write_bytes(b"x")
        watcher.prune_snapshots(self.archive_dir, None)
        self.assertTrue(weird.exists())
        self.assertTrue(readme.exists())

    def test_failed_deletion_is_logged_and_others_still_pruned(self):
        bad = self.archive_dir / _snapshot_name(datetime.timedelta(hours=2))
        bad.write_bytes(b"x")
        other_old = self.archive_dir / _snapshot_name(datetime.timedelta(hours=3))
        other_old.write_bytes(b"x")

        original_unlink = Path.unlink

        def flaky_unlink(self_path, *args, **kwargs):
            if self_path.name == bad.name:
                raise OSError("simulated failure")
            return original_unlink(self_path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", flaky_unlink):
            watcher.prune_snapshots(self.archive_dir, None)

        self.assertTrue(bad.exists())
        self.assertFalse(other_old.exists())

    def test_missing_archive_dir_is_noop(self):
        missing = self.archive_dir / "does-not-exist"
        watcher.prune_snapshots(missing, None)  # must not raise


class TestListBackupFolders(unittest.TestCase):
    def test_lists_only_matching_folders_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "2026.01.02-10.00.00").mkdir()
            (root / "2026.01.01-10.00.00").mkdir()
            (root / "not-a-backup").mkdir()
            names = [f.name for _, f in list_backup_folders(root)]
            self.assertEqual(names, ["2026.01.01-10.00.00", "2026.01.02-10.00.00"])


class TestProcessNewBackups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "backups"
        self.root.mkdir()
        self.archive_dir = Path(self.tmp.name) / "snapshots"
        self.state_path = Path(self.tmp.name) / "state.json"
        self.owner = "22222222-2222-2222-2222-222222222222"

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_seeds_baseline_without_events(self):
        write_backup_folder(self.root, "2026.01.01-00.00.00",
                             build_gvas_with_pal("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", self.owner, 10))
        process_new_backups(self.root, self.archive_dir, self.state_path)
        state = load_state(self.state_path)
        self.assertEqual(state["last_processed"], "2026.01.01-00.00.00")
        self.assertEqual(state["events"], [])
        self.assertTrue((self.archive_dir / "2026.01.01-00.00.00.sav").exists())

    def test_second_run_produces_new_pal_event(self):
        write_backup_folder(self.root, "2026.01.01-00.00.00",
                             build_gvas_with_pal("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", self.owner, 10))
        process_new_backups(self.root, self.archive_dir, self.state_path)

        write_backup_folder(self.root, "2026.01.02-00.00.00",
                             build_gvas_with_pal("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", self.owner, 20))
        process_new_backups(self.root, self.archive_dir, self.state_path)

        state = load_state(self.state_path)
        self.assertEqual(len(state["events"]), 1)
        self.assertEqual(state["events"][0]["id"], 1)
        self.assertEqual(state["events"][0]["level"], 20)
        self.assertEqual(state["events"][0]["snapshot"], "2026.01.02-00.00.00")

    def test_missing_level_sav_is_skipped(self):
        folder = self.root / "2026.01.01-00.00.00"
        folder.mkdir()
        process_new_backups(self.root, self.archive_dir, self.state_path)
        state = load_state(self.state_path)
        self.assertIsNone(state["last_processed"])

    def test_parse_failure_advances_state_and_skips_diff(self):
        write_backup_folder(self.root, "2026.01.01-00.00.00",
                             build_gvas_with_pal("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", self.owner, 10))
        process_new_backups(self.root, self.archive_dir, self.state_path)

        folder = self.root / "2026.01.02-00.00.00"
        folder.mkdir()
        (folder / "Level.sav").write_bytes(b"not a valid palsav file")
        process_new_backups(self.root, self.archive_dir, self.state_path)

        state = load_state(self.state_path)
        self.assertEqual(state["last_processed"], "2026.01.02-00.00.00")
        self.assertEqual(state["events"], [])

    def test_corrupt_zlib_body_advances_state_and_skips_diff(self):
        # A save with a valid palsav header (correct "PlZ" magic, correct
        # save_type) but a corrupt/truncated zlib body underneath. This
        # raises zlib.error from decompress_sav's zlib.decompress call, not
        # a ParseError -- the watcher must still not wedge on it.
        write_backup_folder(self.root, "2026.01.01-00.00.00",
                             build_gvas_with_pal("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", self.owner, 10))
        process_new_backups(self.root, self.archive_dir, self.state_path)

        raw = build_gvas_with_pal("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", self.owner, 20)
        good_body = zlib.compress(raw)
        corrupt_body = good_body[:len(good_body) // 2]  # truncated -> zlib.error on decompress
        header = struct.pack("<II", len(raw), len(corrupt_body)) + b"PlZ" + struct.pack("<B", 0x31)
        folder = self.root / "2026.01.02-00.00.00"
        folder.mkdir()
        (folder / "Level.sav").write_bytes(header + corrupt_body)

        process_new_backups(self.root, self.archive_dir, self.state_path)

        state = load_state(self.state_path)
        self.assertEqual(state["last_processed"], "2026.01.02-00.00.00")
        self.assertEqual(state["events"], [])

    def test_archive_failure_leaves_state_untouched(self):
        write_backup_folder(self.root, "2026.01.01-00.00.00",
                             build_gvas_with_pal("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", self.owner, 10))
        process_new_backups(self.root, self.archive_dir, self.state_path)

        write_backup_folder(self.root, "2026.01.02-00.00.00",
                             build_gvas_with_pal("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", self.owner, 20))
        with mock.patch("watcher.shutil.copy2", side_effect=OSError("disk full")):
            process_new_backups(self.root, self.archive_dir, self.state_path)

        state = load_state(self.state_path)
        self.assertEqual(state["last_processed"], "2026.01.01-00.00.00")

    def test_process_new_backups_prunes_old_snapshots(self):
        old_leftover = self.archive_dir / _snapshot_name(datetime.timedelta(hours=2))
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        old_leftover.write_bytes(b"stale")

        write_backup_folder(self.root, "2026.01.01-00.00.00",
                             build_gvas_with_pal("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", self.owner, 10))
        process_new_backups(self.root, self.archive_dir, self.state_path)

        self.assertFalse(old_leftover.exists())
        self.assertTrue((self.archive_dir / "2026.01.01-00.00.00.sav").exists())


if __name__ == "__main__":
    unittest.main()
