"""Background polling loop for a Palworld dedicated server's own backup
rotation: archives each new Level.sav, decompresses + parses it and the
previous snapshot, diffs them for newly acquired pals, and appends the
resulting events to the persisted state (see state.py).

Palworld's dedicated server writes a complete, already-finished backup
folder on its own rotation -- by the time a folder shows up here it's safe
to read, no mid-write races to guard against. Since the server prunes old
backups on its own, each processed Level.sav is copied into a local archive
directory that isn't subject to that pruning, so history survives and the
watcher can resume correctly after a restart.
"""

import datetime
import logging
import shutil
import time
from pathlib import Path

import decompress
import diff
import gvas
import state as state_module
from binary_reader import ParseError

FOLDER_NAME_FORMAT = "%Y.%m.%d-%H.%M.%S"
POLL_SECONDS = 60

log = logging.getLogger("palsave_api.watcher")


def list_backup_folders(backup_root: Path):
    """Every subfolder matching Palworld's backup naming pattern, oldest first."""
    folders = []
    for child in backup_root.iterdir():
        if not child.is_dir():
            continue
        try:
            timestamp = datetime.datetime.strptime(child.name, FOLDER_NAME_FORMAT)
        except ValueError:
            continue
        folders.append((timestamp, child))
    folders.sort(key=lambda pair: pair[0])
    return folders


def archive_snapshot(sav_path: Path, archive_dir: Path, folder_name: str) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / f"{folder_name}.sav"
    if not dest.exists():
        shutil.copy2(sav_path, dest)
    return dest


def _load_sav(path: Path) -> dict:
    data = path.read_bytes()
    gvas_data = decompress.decompress_sav(data)
    return gvas.parse_gvas(gvas_data)


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
        except ParseError:
            log.exception("[%s] failed to parse, skipping diff for this snapshot", folder.name)

        state["last_processed"] = folder.name
        state["last_snapshot_path"] = str(archived)
        state_module.save_state(state_path, state)


def run_forever(backup_root: Path, archive_dir: Path, state_path: Path) -> None:
    log.info("watching %s every %ds (archive: %s)", backup_root, POLL_SECONDS, archive_dir)
    while True:
        try:
            process_new_backups(backup_root, archive_dir, state_path)
        except Exception:
            log.exception("watcher tick failed, will retry next cycle")
        time.sleep(POLL_SECONDS)
