# `summarize` Post-Run Action — Design

**Status:** Approved (brainstorming) — pending spec review.
**Date:** 2026-06-16

## Goal

After each watcher poll, run the external `video-summarizer` CLI over every
newly-published video — using its already-uploaded R2 `public_url` as the
source — write one markdown analysis per video to a configured directory, and
fire a **single** summary notification for the run. The cheap pass (transcript +
summary + chapters) runs automatically; the expensive `--visual` pass stays
**manual** (run by hand later on a chosen video).

## Why run-level over the R2 URL

The per-video `ACTIONS` registry already contains a no-op `run_summarize` stub
whose own comment explains why it was never implemented: a per-video action
would need the *local* file, which the shell-out engine deletes after upload.
Summarizing the **R2 `public_url`** (a direct `.mp4`) sidesteps that entirely —
and direct media URLs work in `video-summarizer` out of the box (no cookies, no
JS runtime). So this feature is the realization of that stub, just at the
run level. The dead per-video `summarize` stub is **retired** as part of this
work so there is only one thing named "summarize".

## Architecture

A new run-level action in `skills/publish-video/scripts/watcher_actions.py`:

```
summarize_action(run_context, opts, log=None, env=None,
                 run_fn=subprocess.run, send_fn=send_macos_notification) -> dict
```

Registered in `POST_RUN_ACTIONS` as `"summarize"`. It mirrors `mytv_action`'s
shape: read the successful outcomes from `run_context`, do per-item work, and
isolate per-item failures so one bad video never aborts the rest.

`run_fn`, `send_fn`, and `env` are injected for testability (no real subprocess,
notifications, or network in tests).

### Inputs from `run_context`

`run_context["outcomes"]` is a list; each successful one has
`o["ok"] is True` and `o["result"]` carrying at least `platform`, `title`,
`public_url`, `duration_secs`. The action consumes `title` and `public_url`.

### Options (from the `[[post_run]]` config entry)

| key       | default            | meaning                                                            |
|-----------|--------------------|--------------------------------------------------------------------|
| `command` | `"video-summarizer"` | CLI to invoke; an absolute venv path is recommended under launchd. |
| `out`     | `"./analyses"`     | output directory; `~` is expanded.                                 |
| `lang`    | `""`               | `""` → auto-detect; else forced language (e.g. `"zh"`, `"en"`).    |
| `visual`  | `false`            | pass `--visual` (expensive). Off; run by hand instead.             |
| `notify`  | `true`             | send one summary notification at the end of the run.               |
| `title`   | `"video-summarizer"` | notification title.                                              |

## Behavior

1. Collect `items = [o["result"] for o in run_context["outcomes"] if o["ok"]]`.
   If empty → return `{"skipped": "no items"}` (no notification).
2. For each item, build and run:
   `[command, public_url, "--out", out_dir]`, appending `--lang <lang>` if
   `lang` is set and `--visual` if `visual` is true. Pass `env` through to
   `run_fn`.
3. **Success test:** the CLI prints the written `.md` path to stdout. If
   `stdout.strip()` ends with `.md`, count it as written and record
   `{"title", "path"}`. This covers exit code `0` (full success) and exit code
   `1` *when a file was still written* (summary/visual failed but transcript
   rendered).
4. **Per-item failure isolation:** any other result — exit code `2` (config
   error), exit code `1` with no path (transcript failed, no file), or a
   non-`.md` stdout — is logged via `log(...)` and skipped; the loop continues.
5. **Command not found:** a `FileNotFoundError` from `run_fn` is re-raised as
   `RuntimeError(f"summarize: command not found: {command}")`. This aborts the
   action (every item would fail identically); `run_post_run` records the action
   as failed without aborting the watcher run.
6. **Notification:** after the loop, if ≥1 analysis was written **and** `notify`
   is true, send **one** macOS notification via `send_fn`:
   `title=opts["title"]`, message like
   `"3 analyses → ~/video-analyses: Title A, Title B, Title C"`.
7. **Return:** `{"summarized": N, "out": out_dir, "analyses": [{title, path}, …]}`.

## Registry & config changes

- `watcher_actions.py`: add `summarize_action`; add `"summarize": summarize_action`
  to `POST_RUN_ACTIONS`; **remove** `run_summarize` and the `"summarize"` entry
  from the per-video `ACTIONS` registry (the registry becomes empty or keeps only
  real actions).
- `watcher.py` `DEFAULT_CONFIG`: drop the per-video `summarize` action entry; add
  a disabled `summarize` entry to the `post_run` list.
- `watcher.example.toml`: remove the per-video `[[actions]] summarize` block; add
  the `[[post_run]]` summarize block (disabled), documented as below. (Local
  `watcher.toml` is gitignored; edit it too but it is not committed.)
- `REFERENCE.md`: document the new `summarize` post-run action, its options, and
  the `GEMINI_API_KEY` / installed-CLI dependency.

### Example config block

```toml
[[post_run]]
name = "summarize"
enabled = false
command = "video-summarizer"   # absolute path recommended, e.g.
                               # /Users/.../video-summarizer/.venv/bin/video-summarizer
out = "~/video-analyses"
lang = ""                      # "" = auto-detect; or "zh", "en", …
visual = false                 # expensive Gemini Pro pass; run by hand instead
notify = true                  # one summary notification per run
```

## Dependencies & environment

- `video-summarizer` must be installed and reachable at `command`. Under launchd
  the watcher gets a minimal environment, so an **absolute path to the venv
  console script** is recommended.
- The `video-summarizer` CLI reads `GEMINI_API_KEY` from its **environment**; it
  does not auto-load its own `.env`. The watcher therefore must export
  `GEMINI_API_KEY` before invoking the action. The key lives in
  `/Users/kunwu/Workspace/playground/video-summarizer/.env`. The launchd wrapper
  (`run-watcher.example.sh`) already `source`s the publish-video `.env` under
  `set -a`; add the key there or `source` the summarizer's `.env` the same way,
  e.g.:

  ```bash
  set -a
  source "$REPO/.env"
  source "/Users/kunwu/Workspace/playground/video-summarizer/.env"  # GEMINI_API_KEY
  set +a
  ```
- The action itself does not read or require `GEMINI_API_KEY`; it only inherits
  and forwards `env` to the subprocess. A missing key surfaces as a per-item
  failure (CLI exit 2), logged and skipped — the watcher run still completes.

## Testing (stdlib `unittest` + dependency injection)

In `test_watcher.py` `Actions` class — inject `run_fn`, `send_fn`, `env`; never
touch real subprocess/network/notifications.

- `test_summarize_action_no_items_skips` — empty/failed-only outcomes → `{"skipped": ...}`, `run_fn` never called.
- `test_summarize_action_runs_per_item_and_writes` — two items → `run_fn` called twice with the right `public_url`/`--out`; both `.md` paths recorded; `summarized == 2`.
- `test_summarize_action_passes_lang_and_visual` — `lang`/`visual` opts append `--lang`/`--visual`.
- `test_summarize_action_partial_still_counts` — fake `run_fn` returns rc 1 with a `.md` stdout → counted.
- `test_summarize_action_skips_failure_and_isolates` — one item rc 2 / no-path stdout is skipped; the other still recorded; `summarized == 1`.
- `test_summarize_action_command_not_found_raises` — `run_fn` raises `FileNotFoundError` → `RuntimeError`.
- `test_summarize_action_one_notification_with_titles` — after success, `send_fn` called exactly once; message lists all titles.
- `test_summarize_action_notify_disabled` — `notify=false` → `send_fn` never called.
- Plus: removal of the per-video `summarize` stub updates/deletes
  `test_stubs_return_skipped` (or its `summarize` assertion) and the
  `DEFAULT_CONFIG`/`watcher.example.toml` expectations.

Run the full suite (`python3 -m unittest test_publish_video test_watcher`)
before each commit.

## Out of scope (YAGNI)

- Triggering the visual pass automatically or per-video matching — visual is run
  by hand for now.
- Per-video notifications — one run-level summary notification only.
- Feeding the summary text into MyTV descriptions or any other action.
- Auto-loading the summarizer's `.env` from inside the action.
