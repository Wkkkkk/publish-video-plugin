# Design: Parallel publishing for the watcher

Date: 2026-06-15
Status: Approved (brainstorm), pending implementation plan

## Purpose

The watcher currently publishes new items **serially** — `tick` downloads + uploads
one video fully before starting the next. A pass with N new videos takes ~N × one
video, and a single large video is slow on its own. This adds two independent layers
of parallelism: concurrent videos within a pass, and parallel fragment downloads
within a single video.

## Two knobs (independent)

- **`concurrency`** — how many videos download/upload at once. Config key, default **5**;
  CLI `--concurrency N` overrides.
- **`concurrent_fragments`** — passed to yt-dlp as `-N`, parallelizing one video's
  fragment downloads. Config key, default **4**. (Config only; no CLI flag in v1.)

## Approach

A bounded `ThreadPoolExecutor` in `tick`. Each video's real work happens in a
subprocess (`publish_video.py` → yt-dlp/ffmpeg), so threads give true parallelism —
they block on subprocess I/O and the GIL is released during `subprocess.run`. This
avoids multiprocessing/asyncio, which would be overkill since the work is already
out-of-process, and fits the existing dependency-injection design.

Per-video fragment parallelism is a single `-N` flag added to the engine's yt-dlp
download command.

## Components / changes

### Engine — `publish_video.py` (one change)
Add a `--concurrent-fragments N` argument (default `1`, backward-compatible). When
`> 1`, `build_ytdlp_cmd` injects `-N N` into the yt-dlp download command. This is the
only change to the engine; its JSON envelope, exit codes, and other behavior are
untouched. Covered by a unit test on `build_ytdlp_cmd`.

### Watcher — parallel `tick` (`watcher.py`)
Split into two phases:
1. **Listing (serial, unchanged):** for each platform, `list_entries` → `new_entries`,
   collecting all fresh entries across platforms into one work-list. Listing is cheap
   and already bounded by `max_items`; titles are resolved here as today. A listing
   failure for one platform logs and is skipped (existing resilience).
2. **Publish (parallel):** submit every fresh entry to
   `ThreadPoolExecutor(max_workers=cfg["concurrency"])`; each worker runs
   `process_entry` (which shells out to the engine). As each future completes, record
   success in state.

### Publish invocation (`watcher.py`)
`build_publish_cmd` and `run_publish` gain a `concurrent_fragments` parameter and pass
`--concurrent-fragments N` to the engine. `process_entry` reads
`cfg["concurrent_fragments"]`.

### Config (`watcher.py`, `watcher.example.toml`, docs)
`DEFAULT_CONFIG` gains `concurrency: 5` and `concurrent_fragments: 4`. CLI gains
`--concurrency`. The example template and REFERENCE.md document both.

## State safety under concurrency

The one real hazard. Today `tick` mutates the `seen` set and writes `state.json` after
each success, inside the loop. Concurrently that races (lost updates / interleaved
writes). Fix: a `threading.Lock` guarding the "add `entry_key` to `seen` + `save_state`"
step, applied as each future completes. This preserves the crash-safe incremental save,
just serialized. `seen` is read to compute `fresh` **before** the pool starts, so the
dedup snapshot is race-free.

## Error handling

- Per-item failures stay isolated: a worker that fails returns `ok: False`; the item is
  not added to `seen` and retries next pass (unchanged semantics).
- A worker raising is contained per future; other futures continue.
- Listing failures per platform still log + skip.
- Concurrent workers' stderr (engine/yt-dlp output) interleaves on the terminal — this
  is acceptable; each "published → URL" log is a single write.

## What stays the same

Dependency-injection design, the JSON envelope contract, `--once` / loop / `--platform`,
`max_items` (now also bounds the work-list size), title resolution, the published-URL
log, engine-stderr forwarding. **`--dry-run` stays serial** — it lists only, never
publishes, so there is nothing to parallelize.

## Testing

- **Engine:** `build_ytdlp_cmd` includes `-N N` when `concurrent_fragments > 1`, omits it
  when `1`. (`test_publish_video.py`.)
- **Watcher:** `build_publish_cmd` includes `--concurrent-fragments`; `run_publish` passes
  it through; `tick` with `concurrency > 1` still processes every fresh entry, records
  only successes in state, and isolates per-item and per-platform failures. Deterministic
  because deps are fakes (fast, no real threads-of-consequence — the pool runs fake
  callables). (`test_watcher.py`.)

## Out of scope (v1)

- Parallelizing the listing / title-resolution phase (bounded by `max_items`, cheap).
- A CLI flag for `concurrent_fragments` (config only).
- Per-platform concurrency settings (single global pool).
- Rate limiting / backoff for the MyTV action under concurrency (independent POSTs).
