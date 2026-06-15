# Design: Watcher run reporting (summary + log rotation + notify)

Date: 2026-06-15
Status: Approved (brainstorm), pending implementation plan

## Purpose

The watcher's only output today is an unbounded, verbose log file
(`~/.publish-video-watcher/watcher.log`). There is no per-run summary, no log
rotation, and the `notify` action is an unimplemented stub. This adds three
observability improvements so a scheduled (launchd) run is easy to monitor:

1. A one-line per-run summary: `run done: 3 published, 0 failed`.
2. Size-based log rotation so the file does not grow forever.
3. A working `notify` action delivering a macOS Notification Center alert.

## Decisions (from brainstorm)

- **Notify channel:** macOS Notification Center via `osascript`. Self-contained
  (the watcher is a standalone script run by launchd; it cannot call Claude/MCP).
- **Notify trigger:** `activity` — fire when `published > 0` OR `failed > 0` OR a
  listing error occurred; stay silent on idle (0-new) runs. (`failure` and
  `always` also supported as config values.)
- **Rotation:** wrapper-based copytruncate (no sudo, no dependency).
- **yt-dlp `--no-progress`:** confirmed in scope — removes the `[download] N%` spam
  that bloats the log.

## Components / changes

### 1. Per-run summary (`watcher.py`)

`tick()` currently returns a flat list of per-video outcomes. Change it to return
a dict:

```python
{"outcomes": [ {entry, ok, ...}, ... ], "listing_errors": ["youtube", ...]}
```

so a whole-platform listing failure (e.g. expired cookies) is surfaced, not only
per-video publish failures. `tick` already collects listing failures in its
listing loop; capture their platform names into `listing_errors` instead of only
logging them.

New pure helper:

```python
def format_summary(result: dict) -> str:
    outcomes = result["outcomes"]
    published = sum(1 for o in outcomes if o.get("ok"))
    failed = len(outcomes) - published
    line = f"run done: {published} published, {failed} failed"
    n = len(result.get("listing_errors") or [])
    if n:
        line += f" · {n} listing error" + ("s" if n != 1 else "")
    return line
```

`main()` logs `format_summary(result)` after each `tick()` (both `--once` and loop
paths), then invokes notify (below).

### 2. Log rotation (`run-watcher.sh` wrapper)

At the very start of the wrapper, before any output, copytruncate when the log
exceeds a threshold (default 5 MB), keeping one previous generation:

```bash
LOG="$HOME/.publish-video-watcher/watcher.log"
MAX_BYTES=$((5 * 1024 * 1024))
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG")" -gt "$MAX_BYTES" ]; then
    cp "$LOG" "$LOG.1" && : > "$LOG"   # copytruncate: keeps launchd's open fd valid
fi
```

`: > "$LOG"` truncates in place (same inode), so launchd's already-open
`StandardErrorPath`/`StandardOutPath` fd keeps appending correctly. Done before the
run header echo, so nothing from the current run is lost. This is a wrapper-only
change (the wrapper lives outside the repo at
`~/.publish-video-watcher/run-watcher.sh`); the repo keeps a documented template.

### 3. yt-dlp `--no-progress` (`publish_video.py`)

Add `--no-progress` to `build_ytdlp_cmd`'s yt-dlp invocation (always on; progress
bars are noise in a non-interactive context). Covered by a `build_ytdlp_cmd` unit
test asserting the flag is present. No new CLI flag — it is unconditional.

### 4. Notify action (`watcher_actions.py`, `watcher.py`)

The `notify` action becomes **run-level** (driven by the run summary), not a
per-video pipeline action.

`watcher_actions.py`:

```python
def send_macos_notification(title, message, run_fn=subprocess.run) -> None:
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    run_fn(["osascript", "-e", script], capture_output=True, text=True)

def notify_run(result, notify_cfg, message, send_fn=send_macos_notification) -> dict:
    if not notify_cfg.get("enabled"):
        return {"notified": False, "reason": "disabled"}
    published = sum(1 for o in result["outcomes"] if o.get("ok"))
    failed = len(result["outcomes"]) - published
    errors = len(result.get("listing_errors") or [])
    trigger = notify_cfg.get("trigger", "activity")
    should = (
        trigger == "always"
        or (trigger == "failure" and (failed or errors))
        or (trigger == "activity" and (published or failed or errors))
    )
    if not should:
        return {"notified": False, "reason": "trigger not met"}
    send_fn(notify_cfg.get("title", "publish-video watcher"), message)
    return {"notified": True}
```

To keep modules acyclic, `format_summary` lives in `watcher.py` and `notify_run`
(in `watcher_actions.py`) does NOT import it: `main()` computes the summary string
and passes it in as `message`. `notify_run` independently derives the
fire-or-not decision from `result`'s counts. Final wiring in `main()`:

```python
summary = format_summary(result)
log(summary)
deps["notify"](result, cfg["notify"], summary.removeprefix("run done: "))
```

Config moves from a per-video `[[actions]]` stub to a top-level block:

```toml
[notify]
enabled = false
trigger = "activity"   # activity | failure | always
title = "publish-video watcher"
```

`DEFAULT_CONFIG` gains `"notify": {"enabled": False, "trigger": "activity",
"title": "publish-video watcher"}` and drops the `notify` entry from the default
`actions` list. `watcher_actions.ACTIONS` drops `run_notify`. `summarize` stays a
per-video action.

`build_deps()` gains `"notify": watcher_actions.notify_run` so tests can inject a
fake; `send_fn` defaults to the real osascript sender and is faked in unit tests.

## Risk to verify (not assume)

A launchd `Background` agent may be denied Notification Center access. After
implementation, trigger a notification from the launchd context and confirm it
appears. If blocked, fall back to `terminal-notifier` or relax `ProcessType`;
document whichever works. Log-only reporting still functions regardless.

## Error handling

- `notify_run` failures are isolated like other actions: a raising `send_fn` is
  caught at the `main()` call site and logged; it never aborts the run.
- Rotation is best-effort in the wrapper; a failed `cp`/truncate must not stop the
  watcher (guard with `|| true` semantics).

## Testing

- `format_summary`: 0/0, N/0, 0/M, N/M, with and without listing errors;
  singular vs plural "listing error(s)".
- `notify_run`: matrix of trigger (`activity`/`failure`/`always`) × state
  (published-only / failed-only / listing-error-only / idle / disabled), asserting
  whether `send_fn` (a fake) was called.
- `send_macos_notification`: asserts the `osascript -e` command shape and that
  embedded quotes are escaped, using a fake `run_fn`.
- `build_ytdlp_cmd`: includes `--no-progress`.
- `tick`: returns the new `{outcomes, listing_errors}` shape; existing tick tests
  updated accordingly; a listing failure populates `listing_errors`.

All tests deterministic via dependency injection — no real notifications, no real
subprocesses.

## What stays the same

The per-video `actions` pipeline (`mytv`, `summarize`), state-file dedup, parallel
publishing, the JSON/engine contract, `--once`/loop/`--dry-run`. Rotation and the
wrapper live outside the repo (machine-specific); the repo carries a documented
template only.

## Out of scope (v1)

- Non-macOS notification channels (webhook/email/custom command) — config shape
  leaves room to add them as future `trigger`-independent channels.
- Structured (JSON) run reports or metrics.
- Rotating more than one previous generation.
