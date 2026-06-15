# Design: Saved-video → publish watcher

Date: 2026-06-15
Status: Approved (brainstorm), pending implementation plan

## Purpose

A standalone watcher that polls a configured **saved-video source** on YouTube and
Bilibili — Watch Later by default, or a saved/favorites folder — and for each *new*
item runs the existing `publish_video.py` engine, then walks a configurable list of
**post-publish actions** (e.g. register to MyTV, summarize). It runs manually
(`--once`) or on a schedule (a local Claude routine). The existing engine is reused
unchanged; the watcher is a new layer on top.

## Scope

This is the "option 4" auto-trigger explored during brainstorming, reframed: a Claude
Code *hook* fires only inside a live session, so it cannot react to videos appearing in
a Watch Later / saved folder out in the world. The correct mechanism is a polling
**watcher**, optionally scheduled by a local Claude routine.

## Key decisions

- **Source is configurable, not hardcoded to Watch Later.** A source is any
  yt-dlp-enumerable list: Watch Later, a saved/favorites folder (YouTube playlist,
  Bilibili 收藏夹), liked videos, etc. Watch Later is the default value.
- **Source is read-only.** We never modify the user's Watch Later / folder. Dedup lives
  in our own state file — lower auth risk, simpler.
- **Failed publishes are NOT marked seen** — they retry on the next tick.
- **Engine is reused unchanged.** The watcher shells out to `publish_video.py` and parses
  its JSON envelope.
- **Post-publish actions are config-driven, pluggable modules** (generalizes the existing
  `--sink` concept). `mytv` ships wired; `summarize`/`notify` ship as stubs.
- **Both platforms supported** via independent source adapters.

## Components

Each component has one job and a fixed interface, so it can be understood and tested
on its own.

1. **Source adapters** — `sources/youtube.py`, `sources/bilibili.py`.
   Contract: given a *source spec* (e.g. `watch_later` or a playlist/folder URL/ID) and
   the user's cookies, **list** current entries as `{platform, id, url, title}`.
   List-only — no download (yt-dlp `--flat-playlist`). Adding a platform = one new
   adapter file behind this common interface.

2. **State store** — `state.json`. A set of already-handled keys `{platform}:{id}`.
   The watcher diffs the current source listing against this; only new IDs proceed.
   The source itself is never modified.

3. **Engine (reused, unchanged)** — for each new URL, invoke
   `publish_video.py <url>` and parse the stdout JSON envelope
   (`{ ok, failed, results: [{ public_url, duration_secs, ... }] }`).

4. **Action pipeline** — after a successful publish, walk the configured action list.
   Each action is `actions/<name>.py` exposing `run(result, opts) -> dict`, where
   `result` carries `{ local_path?, public_url, title, duration_secs, platform,
   source_id }`. Ships with `mytv` (wraps the existing MyTV sink). `summarize` and
   `notify` ship as stubs to fill in later; `summarize` may internally call Claude.
   Per-action failures are caught and logged and do not block other actions or items.

5. **Watcher orchestrator** — `watcher.py`. Per tick: load config → for each configured
   platform, list its source → diff vs state → for each new item: publish → run actions
   → record in state on success. CLI: `--once` (single pass) | default loop |
   `--platform`, `--dry-run`, `--config`.

## Configuration

One declarative file. Secrets stay where they already live (`.env`, cookie store) — not
in this config.

```yaml
poll_interval_mins: 60            # loop mode only
transcode: false                  # exposes the engine's --transcode at acquire-time
platforms:
  youtube:
    source: watch_later           # or a playlist / saved-folder URL or ID
  bilibili:
    source: watch_later           # or a favorites folder (fid)
actions:
  - mytv:      { enabled: true, channel: 7 }
  - summarize: { enabled: false }
```

Cookies are read via yt-dlp (e.g. `--cookies-from-browser`), since Watch Later and
private folders are only visible to the logged-in account.

## Data flow (one tick)

```
config → for each platform:
  adapter.list(source, cookies) → entries
  new = entries - state
  for item in new:
    publish_video.py item.url → envelope
    if ok:
      for action in enabled_actions: action.run(result, opts)   # failures logged, isolated
      state.add(item.key)
    else:
      log error; do NOT add to state (retry next tick)
```

## Scheduling

- **Manual:** `python3 watcher.py --once`
- **Automatic:** a local Claude `/schedule` routine that runs `watcher.py --once` every
  `poll_interval_mins`. The routine must fire locally so cookies/S3/MyTV creds resolve;
  confirm this at wiring time. If routines turn out to be cloud-only, fall back to an OS
  cron / launchd timer running the same command — the script is standalone either way.

## Error handling

- Per-item resilient (the engine's existing model): one video failing to download/upload
  logs an error; the watcher continues with the rest.
- A failed publish is **not** recorded in state, so it is retried next tick.
- A failing post-publish action is logged but the publish still counts as done and is
  recorded in state.
- Missing config/tools/creds surface a clear message and a non-zero exit (mirrors the
  engine's exit-code 2 convention).

## Testing

Unit tests with adapters mocked (canned source listings) and the engine mocked
(no live network), asserting:
- new-vs-seen diffing,
- state updated only on successful publish,
- action pipeline invoked in configured order with correct input,
- per-item and per-action failure isolation.

Mirrors the existing `unittest` suite under `skills/publish-video/scripts/`.

## Out of scope (YAGNI for v1)

- Removing items from Watch Later / the source folder after publishing.
- Comment-keyword triggering.
- Event-bus / external-subscriber model.
- Cloud execution of the routine.
- Sources beyond a single yt-dlp-enumerable list per platform.

All are reachable later without reworking the v1 structure.
