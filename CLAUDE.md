# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

The service is implemented: `binary_reader.py`, `decompress.py`, `gvas.py`, `diff.py`, `state.py`,
`watcher.py`, `api.py`, and `main.py` per the design spec at
`docs/superpowers/specs/2026-07-20-palsave-api-design.md`. `ooz/bin/libooz.so` still needs to be
built on the Linux deployment host before Oodle-compressed ("PlM") saves can be decompressed there
(see `decompress.py`'s module docstring for the build command) — zlib ("PlZ") saves work without it.

## What this service is

`palsave-api` is a standalone, long-running HTTP service that watches a Palworld dedicated server's
backup rotation, decompresses and parses each new `Level.sav`, diffs consecutive snapshots to find
newly-acquired pals, and exposes those as a small paginated events feed. It ports the
decompress/parse/diff logic from a separate `palsave` sandbox project (`main.py`, `recap.py`,
`watcher.py`) into this API-only service.

- `swee`'s Discord bot is the first consumer (see `swee`'s
  `docs/superpowers/specs/2026-07-20-palfeed-design.md`), but this service is Discord-agnostic —
  other consumers can read the same API without this service knowing about them.
- The split from `swee` exists because decompression/parsing is CPU-bound and must not run inside
  `swee`'s discord.py event loop, and because the logic is reusable beyond Discord.
- The `palsave` sandbox repo is left untouched; nothing here modifies it.

## Planned architecture

- `decompress.py` — zlib + Oodle (`ctypes` + vendored `libooz.so`) container decompression.
  Self-contained; ported from palsave's `main.py` layer.
- `gvas.py` — Gvas tagged-property parser (`parse_gvas`). Pure parsing, no I/O.
- `diff.py` — the *structural* half of palsave's `recap.py::diff_new_pals`: identifies newly
  acquired pals between two snapshots and classifies acquisition type (wild capture / hatched /
  purchased), excluding recruitable human NPCs. Deliberately does **not** port the
  notability-tier opinion (`notability_tier`, `TALENT_TIERS`, "Lucky"/"Awakened"/"Perfect"
  labeling) — that's a Discord-recap-specific judgment call and stays in `swee`.
- `watcher.py` — background polling loop (fixed 60s interval, no env override — matches `swee`'s
  precedent of not exposing every interval as a knob): lists backup folders newer than the last
  processed one, archives each `Level.sav` into local `snapshots/`, decompresses + parses old/new
  snapshots, diffs them, and appends new-pal records to an internal event log with a monotonically
  increasing event ID.
- `api.py` — FastAPI app bound to `127.0.0.1` only (no auth). One endpoint:
  `GET /events/new-pals?since=<id>&limit=<N>` → up to `N` events with `id > since`, oldest first.
- `ooz/bin/libooz.so` — vendored, built from `github.com/zao/ooz` with `--recurse-submodules`,
  `-DOOZ_BUILD_EXE=OFF -DOOZ_BUILD_BUN=OFF -DOOZ_BUILD_VALIDATE=OFF`.

## Configuration

Env vars via `.env` (matches `swee`'s convention):

```
PALSAVE_API_BACKUP_DIR=/home/steam/Steam/steamapps/common/PalServer/Pal/Saved/SaveGames/.../Backup
PALSAVE_API_PORT=8787
```

## Data flow

1. Background task polls `PALSAVE_API_BACKUP_DIR` every 60s from service startup.
2. On first run (no cached state), seed the baseline from the latest existing backup folder without
   generating events — avoids replaying the server's whole backup history as day-one events.
3. Per new folder: copy `Level.sav` into `snapshots/<folder_name>.sav`; decompress + parse both
   previous and new snapshot; diff; append an event per newly-acquired pal with the next event ID.
4. State (last processed folder, last snapshot path, next event ID, event log) persists to disk
   after each successfully processed folder, so a restart resumes correctly.
5. `GET /events/new-pals` reads directly from the persisted event log; each consumer tracks its own
   `since` cursor — no consumer-side state lives on the service.

## Error handling conventions

- Parse failure on a snapshot: log and skip the diff for that tick; state still advances so one bad
  snapshot doesn't wedge the watcher forever.
- Archive I/O failure: leave state untouched so the folder is retried next tick.
- API request against an unreadable/corrupt event log: 500, logged.

## Explicitly out of scope (don't add without a spec update)

- Auth or external network exposure (service is loopback-only until an off-host consumer needs it).
- Notability/highlight logic — that's a `swee`-owned Discord-recap opinion.
- Endpoints beyond `/events/new-pals` (full pal lists, snapshot history, etc.) — deferred per YAGNI.
- CLI entry points mirroring palsave's standalone `main.py`/`recap.py`/`watcher.py` usage — this is
  API-only.
- Non-Linux platform support.

## Testing notes

`gvas.py` and `diff.py` are pure functions and are the priority for unit tests, mirroring `swee`'s
`tests/test_palworld_settings.py` style, if a reference `.sav` fixture is available. The
acquisition-classification rules `diff.py` needs are documented in palsave's project memory
(`owned_time_field_semantics.md`, `recap_notability_rules.md`) — note the notability rules
themselves now live in `swee`, only the underlying classification rules apply here.
