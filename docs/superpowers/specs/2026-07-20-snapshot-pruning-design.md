# snapshot-pruning — bound `snapshots/` growth

## Summary

`watcher.py`'s `snapshots/` archive directory is append-only: every new Palworld backup folder's
`Level.sav` gets copied in and nothing is ever removed. In production, Palworld's own auto-save
interval (~30s) means this grows by roughly 2,880 files/day indefinitely. Since nothing in
`palsave-api`'s pipeline reads any snapshot other than the single most recently processed one
(`state["last_snapshot_path"]`, needed for the next diff), the rest of the archive exists purely
as a manual/forensic lookback buffer — not a functional requirement. This adds a small pruning
step to the watcher loop that keeps a 1-hour lookback window and discards everything older.

## Behavior

After each `process_new_backups` tick's archive/diff loop, prune `snapshots/`:

- Parse each `*.sav` filename in the archive directory using the same `FOLDER_NAME_FORMAT`
  (`%Y.%m.%d-%H.%M.%S`) `list_backup_folders` already uses to parse backup folder names (the
  archived filename is `<folder_name>.sav`, so the stem parses the same way).
- Delete any file whose parsed timestamp is older than `now - timedelta(hours=1)`.
- **Exception:** never delete the file matching `state["last_snapshot_path"]`, regardless of age.
  This is required for correctness, not just a safety margin: if the service is ever down for
  longer than an hour, the snapshot the next diff needs to compare against could otherwise be
  older than the retention window by the time processing resumes, and pruning it would silently
  break that diff (the file would already be gone by the time `_load_sav(previous_path)` tries to
  read it, raising `OSError` — which Task 7's broadened `except Exception` would catch and log as
  a skipped tick, quietly losing that comparison rather than crashing, but still an avoidable
  loss).
- A file whose name doesn't match `FOLDER_NAME_FORMAT + ".sav"` is left untouched (defensive — no
  guessing at unknown files).
- A failed deletion (`OSError`, e.g. permissions) is logged and skipped; it does not stop pruning
  of the remaining files or fail the tick, matching the watcher's existing "log and continue"
  error-handling philosophy.

Retention is a hardcoded `SNAPSHOT_RETENTION = timedelta(hours=1)` module constant in
`watcher.py`, no env override — matching the existing `POLL_SECONDS` precedent of not exposing
every interval as a knob.

## Rationale for the 1-hour window (not tiered, not longer)

A tiered retention scheme (mirroring Palworld's own daily/hourly/10-min/30s backup rotation) was
considered and rejected: nothing in `palsave-api` currently reads historical snapshots beyond the
single most recent one, so any retention beyond a short lookback buffer has no functional
justification — it would only serve a manual-forensics use case that doesn't currently exist
(matches the project's existing YAGNI stance: "No endpoints beyond `/events/new-pals` yet...
deferred until an actual consumer needs them"). A 1-hour buffer was chosen as a middle ground
between "keep literally nothing beyond what's needed for the next diff" and "keep a meaningfully
useful window for manual inspection if something goes wrong" — enough to look back at what
happened in roughly the last hour without unbounded growth.

## Testing

`prune_snapshots` is a pure-ish function (filesystem I/O, no external state beyond what's passed
in) — testable with a temp directory containing a mix of filenames:
- A recent file (within the window): kept.
- An old file (outside the window): deleted.
- An old file that matches the protected `last_snapshot_path`: kept despite its age.
- A file with a non-matching name: left untouched.
- A deletion that raises `OSError` (simulated via a permission-denied file or a mocked `unlink`):
  logged, tick continues, other deletions still happen.

## Out of scope

- No env-configurable retention window (matches `POLL_SECONDS` precedent).
- No pruning of `state.json`'s event log — a separate, already-acknowledged concern, not addressed
  here.
- No tiered/graduated retention — rejected per the rationale above.
