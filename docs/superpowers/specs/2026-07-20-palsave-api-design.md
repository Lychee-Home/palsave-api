# palsave-api — Palworld save-parsing service

## Summary

A standalone service, `palsave-api`, that ports the decompress/parse/diff logic currently living
in the `palsave` sandbox project (`main.py`, `recap.py`, `watcher.py`) into a long-running process
with an HTTP API. It watches the Palworld dedicated server's own backup rotation, decompresses and
parses each new `Level.sav`, diffs consecutive snapshots to find newly-acquired pals, and exposes
those as a small paginated events feed. `swee`'s Discord bot is the first consumer (see
`swee`'s `docs/superpowers/specs/2026-07-20-palfeed-design.md` for how it polls this API and posts
recap embeds), but the service itself is Discord-agnostic — a future website or other tool could
consume the same API without this service knowing or caring.

This is a new repo, `palsave-api`, separate from both `swee` and the `palsave` sandbox project
(which is left untouched). The split exists because the heavy CPU-bound decompression/parsing work
should not run inside `swee`'s discord.py process (it would contend with or block the bot's event
loop), and because this logic is genuinely reusable beyond Discord.

## Architecture

- `decompress.py` — port of `decompress_sav`: zlib + Oodle (via `ctypes` + vendored `libooz.so`)
  container decompression. Self-contained, same as palsave's `main.py` layer.
- `gvas.py` — port of the Gvas tagged-property parser (`parse_gvas` and friends). Pure parsing,
  no I/O.
- `diff.py` — port of the *structural* half of `recap.py`'s `diff_new_pals`: identifies newly
  acquired pals between two snapshots and classifies acquisition type (wild capture / hatched /
  purchased), excluding recruitable human NPCs. Does **not** port the notability-tier opinion
  (`notability_tier`, `TALENT_TIERS`, "Lucky"/"Awakened"/"Perfect" labeling) — that's a
  Discord-recap-specific judgment call, not a save-parsing fact, and stays in `swee`.
- `watcher.py` — background polling loop (60s, matches palsave's existing `DEFAULT_POLL_SECONDS`
  convention closely enough to keep behavior familiar): lists backup folders newer than the last
  processed one, archives each `Level.sav` into a local `snapshots/` directory (the server prunes
  its own backups, so this survives that pruning), decompresses + parses old and new snapshots,
  diffs them, and appends the resulting new-pal records to an internal event log — each record
  assigned the next monotonically increasing integer event ID as it's produced, in
  snapshot-processing order.
- `api.py` — FastAPI app, binds to `127.0.0.1` only (no auth; nothing off-host can reach it). One
  endpoint for now:
  - `GET /events/new-pals?since=<id>&limit=<N>` → up to `N` events with `id > since`, oldest
    first. Each event: `id`, `character_id`, `level`, `talent_hp`, `talent_shot`,
    `talent_defense`, `acquisition_type`, `owner_player_uid`, `is_rare_pal`, `is_awakening`,
    `snapshot` (source backup folder name, for traceability).
- `ooz/bin/libooz.so` — same build-from-source vendoring `swee` was originally going to need:
  built from `github.com/zao/ooz` with `--recurse-submodules`,
  `-DOOZ_BUILD_EXE=OFF -DOOZ_BUILD_BUN=OFF -DOOZ_BUILD_VALIDATE=OFF`, copied to `ooz/bin/`.

## Configuration

Env vars loaded from a `.env` file, matching `swee`'s convention:

```
PALSAVE_API_BACKUP_DIR=/home/steam/Steam/steamapps/common/PalServer/Pal/Saved/SaveGames/.../Backup
PALSAVE_API_PORT=8787
```

Poll interval fixed at 60s, no separate env var — matches the precedent set by `swee`'s own
tickers (`stats_ticker`, `release_ticker`) of not exposing every interval as a knob.

## Data flow

1. Background task loop, started at service startup, polls `PALSAVE_API_BACKUP_DIR` every 60s.
2. On first run (no cached state), seed the baseline from the latest existing backup folder
   without generating any events — avoids replaying the server's whole backup history as
   day-one events (same behavior palsave's `watcher.py` already has).
3. For each new folder: copy `Level.sav` into `snapshots/<folder_name>.sav`; decompress + parse
   both the previous and new snapshot; diff via `diff.py`; for each newly acquired pal, append an
   event record with the next event ID.
4. Internal state (last processed folder, last snapshot path, next event ID, and the event log
   itself) persists to disk after each successfully processed folder, so a restart resumes
   correctly.
5. `GET /events/new-pals` reads directly from the persisted event log — no separate consumer-side
   state on the service; each consumer (swee, eventually others) tracks its own `since` cursor.

## Error handling

- Parse failure on a given snapshot: log and skip the diff for that tick; state still advances so
  one bad snapshot doesn't wedge the watcher forever (same as palsave's `watcher.py` today).
- Archive I/O failure: leave state untouched so the folder is retried next tick.
- API requests for an unreadable/corrupt event log: 500, logged — not expected to happen in
  practice since the log is only ever appended to by the same process that serves it.

## Testing

`gvas.py` and `diff.py` are pure functions — worth unit tests mirroring `swee`'s
`tests/test_palworld_settings.py` style if a reference `.sav` fixture is available. Nice-to-have,
not a blocker: the original palsave code was already hand-validated against a reference save (see
palsave's project memory: `owned_time_field_semantics.md`, `recap_notability_rules.md` — note the
notability rules themselves now live in swee, but the underlying acquisition-classification rules
this service still needs are documented there too).

## Out of scope

- No changes to the `palsave` repo — left as-is.
- No notability/highlight logic — that's a Discord-recap opinion and lives in the `swee` consumer.
- No auth / external network exposure yet — add if/when a consumer needs to reach this from
  off-host (e.g. a website not co-located on the same server).
- No endpoints beyond `/events/new-pals` yet — broader query endpoints (full pal lists, snapshot
  history, etc.) are deferred until an actual consumer needs them, per YAGNI.
- No CLI entry points ported (palsave's standalone `main.py`/`recap.py`/`watcher.py` usage) —
  this is an API-only service.
- No support for platforms other than the Linux host this is deployed on (matches `swee`'s own
  Linux-only assumption).
