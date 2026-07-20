# snapshot-pruning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound `snapshots/`'s unbounded growth by pruning archived `.sav` files older than 1 hour,
except the one the next diff still needs.

**Architecture:** A single new function, `prune_snapshots`, added to `watcher.py` (no new files)
and wired into `process_new_backups` via a `try/finally` so it runs on every tick regardless of
which code path that tick took.

**Tech Stack:** Python stdlib only (`datetime`, `pathlib`) — no new dependencies.

## Global Constraints

- Retention is a hardcoded `SNAPSHOT_RETENTION = datetime.timedelta(hours=1)` module constant in
  `watcher.py`, no env override — matches the existing `POLL_SECONDS` precedent.
- The file matching `state["last_snapshot_path"]` must never be deleted regardless of age — it's
  needed for the next diff.
- A file whose name doesn't match `FOLDER_NAME_FORMAT + ".sav"` is left untouched.
- A failed deletion (`OSError`) is logged and skipped; it must not stop pruning of the remaining
  files or fail the tick.
- Test runner: `python -m unittest discover tests -v`.

---

## Task 1: `prune_snapshots` in watcher.py, wired into `process_new_backups`

**Files:**
- Modify: `watcher.py`
- Modify: `tests/test_watcher.py`

**Interfaces:**
- Consumes: nothing new — uses `Path`, `datetime` (stdlib) and the existing `FOLDER_NAME_FORMAT`
  constant already in `watcher.py`.
- Produces: `watcher.prune_snapshots(archive_dir: Path, protect_path: str | None) -> None`,
  `watcher.SNAPSHOT_RETENTION: datetime.timedelta`. Called internally by `process_new_backups`;
  not consumed by any other module.

- [ ] **Step 1: Write the failing tests for `prune_snapshots`**

```python
# add to tests/test_watcher.py, near the top-level test classes

import datetime
from unittest import mock


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
```

Also add this integration test to `TestProcessNewBackups` (reuse its existing `setUp`/`tearDown`
and helpers already in the file — `write_backup_folder`, `build_gvas_with_pal`, `self.owner`):

```python
    def test_process_new_backups_prunes_old_snapshots(self):
        old_leftover = self.archive_dir / _snapshot_name(datetime.timedelta(hours=2))
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        old_leftover.write_bytes(b"stale")

        write_backup_folder(self.root, "2026.01.01-00.00.00",
                             build_gvas_with_pal("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", self.owner, 10))
        process_new_backups(self.root, self.archive_dir, self.state_path)

        self.assertFalse(old_leftover.exists())
        self.assertTrue((self.archive_dir / "2026.01.01-00.00.00.sav").exists())
```

Note: `test_watcher.py` currently does `from watcher import list_backup_folders,
process_new_backups` (function-level imports, no `watcher` module alias) — add `import watcher` at
the top of the file so `watcher.prune_snapshots`/`watcher.FOLDER_NAME_FORMAT` are reachable, and
add `process_new_backups` to `TestProcessNewBackups`'s existing imports as it already is.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_watcher -v`
Expected: FAIL — `AttributeError: module 'watcher' has no attribute 'prune_snapshots'` (and the new
integration test fails the same way, or on the missing `old_leftover` cleanup assertion).

- [ ] **Step 3: Add `SNAPSHOT_RETENTION` and `prune_snapshots` to `watcher.py`**

Add near the other module constants (after `POLL_SECONDS`):

```python
SNAPSHOT_RETENTION = datetime.timedelta(hours=1)
```

Add as a new top-level function, after `archive_snapshot` and before `_load_sav`:

```python
def prune_snapshots(archive_dir: Path, protect_path) -> None:
    """Delete archived snapshots older than SNAPSHOT_RETENTION, except the one the next diff
    still needs (protect_path, typically state["last_snapshot_path"])."""
    if not archive_dir.exists():
        return
    cutoff = datetime.datetime.now() - SNAPSHOT_RETENTION
    protect = Path(protect_path).resolve() if protect_path else None
    for sav_path in archive_dir.glob("*.sav"):
        try:
            timestamp = datetime.datetime.strptime(sav_path.stem, FOLDER_NAME_FORMAT)
        except ValueError:
            continue  # not one of ours, leave it alone
        if timestamp >= cutoff:
            continue
        if protect is not None and sav_path.resolve() == protect:
            continue
        try:
            sav_path.unlink()
        except OSError:
            log.exception("failed to prune %s", sav_path)
```

- [ ] **Step 4: Wire `prune_snapshots` into `process_new_backups`**

Current `process_new_backups` (full function, for reference):

```python
def process_new_backups(backup_root: Path, archive_dir: Path, state_path: Path) -> None:
    state = state_module.load_state(state_path)
    folders = list_backup_folders(backup_root)
    if not folders:
        return

    if state["last_processed"] is None:
        # First ever run: seed the baseline from the latest existing backup
        # rather than replaying the server's whole backup history as if it
        # all just happened.
        timestamp, folder = folders[-1]
        sav_path = folder / "Level.sav"
        if not sav_path.exists():
            log.warning("[%s] skip: no Level.sav found", folder.name)
            return
        archived = archive_snapshot(sav_path, archive_dir, folder.name)
        state["last_snapshot_path"] = str(archived)
        log.info("[%s] baseline snapshot (first run), nothing to diff against yet", folder.name)
        state["last_processed"] = folder.name
        state_module.save_state(state_path, state)
        return

    new_folders = [(ts, f) for ts, f in folders if f.name > state["last_processed"]]

    for timestamp, folder in new_folders:
        sav_path = folder / "Level.sav"
        if not sav_path.exists():
            log.warning("[%s] skip: no Level.sav found", folder.name)
            continue

        try:
            archived = archive_snapshot(sav_path, archive_dir, folder.name)
        except OSError:
            log.exception("[%s] failed to archive, will retry next cycle", folder.name)
            break  # leave state untouched so this folder is retried

        previous_path = state.get("last_snapshot_path")
        try:
            old_snapshot = _load_sav(Path(previous_path))
            new_snapshot = _load_sav(archived)
            new_events = diff.diff_new_pals(old_snapshot, new_snapshot)
            for event in new_events:
                event["snapshot"] = folder.name
            state_module.append_events(state, new_events)
        except Exception:
            log.exception("[%s] failed to load/parse, skipping diff for this snapshot", folder.name)

        state["last_processed"] = folder.name
        state["last_snapshot_path"] = str(archived)
        state_module.save_state(state_path, state)
```

Replace it with this version — the only change is wrapping the existing body (unindented as-is,
just re-indented one level) in `try: ... finally: prune_snapshots(...)`, so pruning runs exactly
once per call regardless of which `return`/`break` path was taken or whether an unexpected
exception occurred:

```python
def process_new_backups(backup_root: Path, archive_dir: Path, state_path: Path) -> None:
    state = state_module.load_state(state_path)
    try:
        folders = list_backup_folders(backup_root)
        if not folders:
            return

        if state["last_processed"] is None:
            # First ever run: seed the baseline from the latest existing backup
            # rather than replaying the server's whole backup history as if it
            # all just happened.
            timestamp, folder = folders[-1]
            sav_path = folder / "Level.sav"
            if not sav_path.exists():
                log.warning("[%s] skip: no Level.sav found", folder.name)
                return
            archived = archive_snapshot(sav_path, archive_dir, folder.name)
            state["last_snapshot_path"] = str(archived)
            log.info("[%s] baseline snapshot (first run), nothing to diff against yet", folder.name)
            state["last_processed"] = folder.name
            state_module.save_state(state_path, state)
            return

        new_folders = [(ts, f) for ts, f in folders if f.name > state["last_processed"]]

        for timestamp, folder in new_folders:
            sav_path = folder / "Level.sav"
            if not sav_path.exists():
                log.warning("[%s] skip: no Level.sav found", folder.name)
                continue

            try:
                archived = archive_snapshot(sav_path, archive_dir, folder.name)
            except OSError:
                log.exception("[%s] failed to archive, will retry next cycle", folder.name)
                break  # leave state untouched so this folder is retried

            previous_path = state.get("last_snapshot_path")
            try:
                old_snapshot = _load_sav(Path(previous_path))
                new_snapshot = _load_sav(archived)
                new_events = diff.diff_new_pals(old_snapshot, new_snapshot)
                for event in new_events:
                    event["snapshot"] = folder.name
                state_module.append_events(state, new_events)
            except Exception:
                log.exception("[%s] failed to load/parse, skipping diff for this snapshot", folder.name)

            state["last_processed"] = folder.name
            state["last_snapshot_path"] = str(archived)
            state_module.save_state(state_path, state)
    finally:
        prune_snapshots(archive_dir, state.get("last_snapshot_path"))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_watcher -v`
Expected: PASS — all pre-existing tests plus the 6 new `TestPruneSnapshots` tests plus the new
`test_process_new_backups_prunes_old_snapshots` integration test.

- [ ] **Step 6: Run the full suite**

Run: `python -m unittest discover tests -v`
Expected: PASS, no regressions (53 pre-existing + 7 new = 60 tests).

- [ ] **Step 7: Commit**

```bash
git add watcher.py tests/test_watcher.py
git commit -m "Prune snapshots/ older than 1 hour, protecting the in-flight diff snapshot"
```

---

## Self-Review Notes

- **Spec coverage:** every behavior in the design spec is covered — 1-hour retention constant, the
  `last_snapshot_path` protection (with the specific rationale about downtime-longer-than-window
  from the spec directly informing a test), non-matching-filename skip, failed-deletion
  log-and-continue, and running the prune step every tick via `finally` (covers the "no new
  folders" early-return path too, which the spec's rationale implies should still be covered since
  pruning isn't conditional on new backups arriving).
- **Placeholder scan:** no TBD/TODO; every step has complete code.
- **Type consistency:** `prune_snapshots(archive_dir: Path, protect_path)` matches its one call
  site in `process_new_backups` (`state.get("last_snapshot_path")`, which is `str | None`) and its
  test calls (`None` or `str(old)`).
