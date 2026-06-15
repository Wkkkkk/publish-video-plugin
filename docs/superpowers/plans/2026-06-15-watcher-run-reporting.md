# Watcher Run Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the launchd-scheduled watcher observable output — a one-line per-run summary, size-based log rotation, quieter yt-dlp, and a working macOS notify action.

**Architecture:** Four independent changes. `publish_video.py` gains an unconditional `--no-progress` yt-dlp flag. `watcher.py` changes `tick()` to return `{outcomes, listing_errors}`, adds a pure `format_summary()`, logs it in `main()`, and calls a run-level notifier. `watcher_actions.py` replaces the per-video `notify` stub with `send_macos_notification()` + `notify_run()`. The wrapper script (outside the repo) gets copytruncate rotation; the repo keeps a documented template.

**Tech Stack:** Python 3 stdlib (`unittest`, `subprocess`, dependency injection), `tomllib`, bash (launchd wrapper), `osascript`.

**Spec:** `docs/superpowers/specs/2026-06-15-watcher-run-reporting-design.md`

**Test commands** (run from `skills/publish-video/scripts/`):
- Engine: `python3 -m unittest test_publish_video -v`
- Watcher: `python3 -m unittest test_watcher -v`

---

### Task 1: yt-dlp `--no-progress`

**Files:**
- Modify: `skills/publish-video/scripts/publish_video.py` (`build_ytdlp_cmd`, ~line 120)
- Test: `skills/publish-video/scripts/test_publish_video.py`

- [ ] **Step 1: Write the failing test**

Add to the `Helpers` class in `test_publish_video.py`:

```python
    def test_build_ytdlp_cmd_no_progress(self):
        cmd = v.build_ytdlp_cmd("URL", "/o.mp4", None, "vcodec:h264")
        self.assertIn("--no-progress", cmd)
        self.assertEqual(cmd[-1], "URL")  # flags stay before the "--" guard
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_publish_video.Helpers.test_build_ytdlp_cmd_no_progress -v`
Expected: FAIL — `'--no-progress' not found in [...]`

- [ ] **Step 3: Add the flag**

In `build_ytdlp_cmd`, the command list literal currently starts:

```python
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f",
        AVC1_FORMAT,
```

Change the head of the list to include `--no-progress` right after `--no-playlist`:

```python
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--no-progress",  # progress bars are noise in non-interactive/log output
        "-f",
        AVC1_FORMAT,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_publish_video -v`
Expected: PASS (all engine tests, including the existing `build_ytdlp_cmd` ones).

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/publish_video.py skills/publish-video/scripts/test_publish_video.py
git commit -m "feat: pass --no-progress to yt-dlp (quieter logs)"
```

---

### Task 2: `format_summary()` helper

**Files:**
- Modify: `skills/publish-video/scripts/watcher.py` (add helper near `process_entry`)
- Test: `skills/publish-video/scripts/test_watcher.py` (the `Orchestrate` class)

A "result" is the new `tick()` return shape: `{"outcomes": [{"ok": bool, ...}], "listing_errors": [str, ...]}`. This task adds the pure formatter only; `tick()` is changed in Task 3.

- [ ] **Step 1: Write the failing tests**

Add to the `Orchestrate` class in `test_watcher.py`:

```python
    def test_format_summary_counts(self):
        result = {"outcomes": [{"ok": True}, {"ok": True}, {"ok": False}], "listing_errors": []}
        self.assertEqual(watcher.format_summary(result), "run done: 2 published, 1 failed")

    def test_format_summary_idle(self):
        self.assertEqual(watcher.format_summary({"outcomes": [], "listing_errors": []}),
                         "run done: 0 published, 0 failed")

    def test_format_summary_one_listing_error(self):
        result = {"outcomes": [{"ok": True}], "listing_errors": ["youtube"]}
        self.assertEqual(watcher.format_summary(result),
                         "run done: 1 published, 0 failed · 1 listing error")

    def test_format_summary_two_listing_errors(self):
        result = {"outcomes": [], "listing_errors": ["youtube", "bilibili"]}
        self.assertEqual(watcher.format_summary(result),
                         "run done: 0 published, 0 failed · 2 listing errors")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_watcher.Orchestrate.test_format_summary_counts -v`
Expected: FAIL — `AttributeError: module 'watcher' has no attribute 'format_summary'`

- [ ] **Step 3: Implement `format_summary`**

Add to `watcher.py` (place it just above `def tick(`):

```python
def format_summary(result: dict) -> str:
    outcomes = result.get("outcomes", [])
    published = sum(1 for o in outcomes if o.get("ok"))
    failed = len(outcomes) - published
    line = f"run done: {published} published, {failed} failed"
    n = len(result.get("listing_errors") or [])
    if n:
        line += f" · {n} listing error" + ("s" if n != 1 else "")
    return line
```

(`·` is the `·` middle dot.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_watcher -v`
Expected: PASS for the four new `test_format_summary_*` tests (existing tests may still pass; `tick` is unchanged this task).

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: add format_summary() run-summary helper"
```

---

### Task 3: `tick()` returns `{outcomes, listing_errors}`

**Files:**
- Modify: `skills/publish-video/scripts/watcher.py` (`tick`, ~lines 119-151; `main` dry-run/once/loop paths)
- Test: `skills/publish-video/scripts/test_watcher.py` (`Orchestrate` class — update existing tick assertions)

Currently `tick()` returns `list(pool.map(work, fresh_all))` (a flat list) and logs listing failures without returning them. Change it to collect listing-error platform names and return a dict.

- [ ] **Step 1: Update the two return-using tick tests + extend the listing-failure test**

The test module uses `import watcher as w` and `_base_deps(overrides)` (overrides is a required dict arg). Two existing `Orchestrate` tests capture `tick`'s return into `handled` and treat it as a list — update both to `handled["outcomes"]`.

In `test_tick_processes_all_fresh_via_pool` (currently ends):

```python
        handled = w.tick(cfg, "/p.py", deps, log=lambda m: None)
        self.assertEqual(len(handled), 3)
        self.assertTrue(all(o["ok"] for o in handled))
        self.assertEqual(saved["keys"], {"youtube:v0", "youtube:v1", "youtube:v2"})
```

change the three assertion lines to:

```python
        handled = w.tick(cfg, "/p.py", deps, log=lambda m: None)
        self.assertEqual(len(handled["outcomes"]), 3)
        self.assertTrue(all(o["ok"] for o in handled["outcomes"]))
        self.assertEqual(handled["listing_errors"], [])
        self.assertEqual(saved["keys"], {"youtube:v0", "youtube:v1", "youtube:v2"})
```

In `test_tick_contains_worker_exception_and_others_continue` (currently):

```python
        handled = w.tick(cfg, "/p.py", deps, log=lambda m: None)
        by_id = {o["entry"]["id"]: o for o in handled}
```

change the `by_id` line to:

```python
        handled = w.tick(cfg, "/p.py", deps, log=lambda m: None)
        by_id = {o["entry"]["id"]: o for o in handled["outcomes"]}
```

(The other two tick tests — `test_tick_marks_only_successful_seen` and `test_tick_isolates_listing_failure` — call `w.tick(...)` without using the return, so they keep working. Extend the latter to assert the new field: in `test_tick_isolates_listing_failure`, change the final call+assert from)

```python
        deps = _base_deps({"list_entries": list_entries, "publish": publish})
        w.tick(cfg, "/p.py", deps, log=lambda m: None)
        self.assertEqual(published["count"], 1)  # bilibili still processed despite youtube failing
```

to:

```python
        deps = _base_deps({"list_entries": list_entries, "publish": publish})
        result = w.tick(cfg, "/p.py", deps, log=lambda m: None)
        self.assertEqual(published["count"], 1)  # bilibili still processed despite youtube failing
        self.assertEqual(result["listing_errors"], ["youtube"])  # failed platform captured
        self.assertEqual(len(result["outcomes"]), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_watcher -v`
Expected: the updated tick tests FAIL (TypeError: list indices / `KeyError: 'outcomes'`) because `tick` still returns a list.

- [ ] **Step 3: Change `tick` to return the dict**

Replace the body of `tick` (current version returns `list(pool.map(work, fresh_all))`). Update the listing loop to collect errors, and the return:

```python
def tick(cfg, script_path, deps, log) -> dict:
    seen = deps["load_state"](cfg["state_path"])
    fresh_all = []
    listing_errors = []
    for platform, pconf in cfg["platforms"].items():
        try:
            entries = deps["list_entries"](platform, pconf["source"], cfg["cookies_browser"],
                                           max_items=cfg["max_items"])
        except Exception as e:  # one platform's listing failing must not stop the others
            log(f"listing {platform} failed: {e}")
            listing_errors.append(platform)
            continue
        fresh = deps["new_entries"](entries, seen)
        log(f"{platform}: {len(entries)} listed, {len(fresh)} new")
        fresh_all.extend(fresh)

    lock = threading.Lock()

    def work(entry):
        try:
            outcome = process_entry(entry, cfg, script_path, deps, log)
        except Exception as e:  # contain per item; other workers keep going
            log(f"error processing {entry.get('url')}: {e}")
            return {"entry": entry, "ok": False, "error": str(e)}
        if outcome["ok"]:
            with lock:  # serialize state mutation + write across workers (crash-safe)
                seen.add(deps["entry_key"](entry))
                deps["save_state"](cfg["state_path"], seen)
        return outcome

    workers = max(1, cfg["concurrency"])
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(work, fresh_all))
    return {"outcomes": outcomes, "listing_errors": listing_errors}
```

- [ ] **Step 4: Update `main()` callers of `tick`**

`main()` calls `tick(...)` in both the `--once` and loop paths but **ignores the return value**, so it keeps working unchanged when `tick` now returns a dict. **No `main()` change in this task** — the summary log + notify are wired in Task 5 via `run_once`. Leave `main()` as-is here.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest test_watcher -v`
Expected: PASS (all watcher tests, including the two updated return-using tick tests and the extended listing-failure test).

- [ ] **Step 6: Commit**

```bash
git add skills/publish-video/scripts/watcher.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: tick() returns {outcomes, listing_errors}"
```

---

### Task 4: macOS notify — `send_macos_notification` + `notify_run`

**Files:**
- Modify: `skills/publish-video/scripts/watcher_actions.py` (replace `run_notify`; drop it from `ACTIONS`)
- Test: `skills/publish-video/scripts/test_watcher.py` (`Actions` class)

- [ ] **Step 1: Update the stale stub test, then add the failing notify tests**

`test_watcher.py` aliases the module as `import watcher_actions as act`. First, the existing `Actions.test_stubs_return_skipped` calls `act.run_notify`, which this task deletes — update it to only check `summarize`:

```python
    def test_stubs_return_skipped(self):
        self.assertIn("skipped", act.run_summarize(SAMPLE_RESULT, {}))
```

Then add these tests to the `Actions` class:

```python
    def test_send_macos_notification_command_shape(self):
        calls = []
        act.send_macos_notification(
            "publish-video watcher", '2 published, 0 failed',
            run_fn=lambda cmd, **kw: calls.append(cmd))
        self.assertEqual(calls[0][0], "osascript")
        self.assertEqual(calls[0][1], "-e")
        self.assertIn('display notification "2 published, 0 failed"', calls[0][2])
        self.assertIn('with title "publish-video watcher"', calls[0][2])

    def test_send_macos_notification_escapes_quotes(self):
        calls = []
        act.send_macos_notification(
            't', 'say "hi"', run_fn=lambda cmd, **kw: calls.append(cmd))
        self.assertIn(r'\"hi\"', calls[0][2])

    def test_notify_run_disabled(self):
        sent = []
        out = act.notify_run(
            {"outcomes": [{"ok": True}], "listing_errors": []},
            {"enabled": False, "trigger": "activity"}, "1 published, 0 failed",
            send_fn=lambda *a: sent.append(a))
        self.assertFalse(out["notified"])
        self.assertEqual(sent, [])

    def test_notify_run_activity_fires_on_publish(self):
        sent = []
        out = act.notify_run(
            {"outcomes": [{"ok": True}], "listing_errors": []},
            {"enabled": True, "trigger": "activity", "title": "T"}, "1 published, 0 failed",
            send_fn=lambda *a: sent.append(a))
        self.assertTrue(out["notified"])
        self.assertEqual(sent, [("T", "1 published, 0 failed")])

    def test_notify_run_activity_silent_on_idle(self):
        sent = []
        out = act.notify_run(
            {"outcomes": [], "listing_errors": []},
            {"enabled": True, "trigger": "activity"}, "0 published, 0 failed",
            send_fn=lambda *a: sent.append(a))
        self.assertFalse(out["notified"])
        self.assertEqual(sent, [])

    def test_notify_run_failure_trigger_only_on_failure(self):
        sent = []
        cfg = {"enabled": True, "trigger": "failure"}
        # success-only -> silent
        act.notify_run({"outcomes": [{"ok": True}], "listing_errors": []},
                       cfg, "m", send_fn=lambda *a: sent.append(a))
        self.assertEqual(sent, [])
        # a failure -> fires
        act.notify_run({"outcomes": [{"ok": False}], "listing_errors": []},
                       cfg, "m", send_fn=lambda *a: sent.append(a))
        self.assertEqual(len(sent), 1)
        # a listing error alone -> fires
        act.notify_run({"outcomes": [], "listing_errors": ["youtube"]},
                       cfg, "m", send_fn=lambda *a: sent.append(a))
        self.assertEqual(len(sent), 2)

    def test_notify_run_always_fires_on_idle(self):
        sent = []
        act.notify_run({"outcomes": [], "listing_errors": []},
                       {"enabled": True, "trigger": "always"}, "m",
                       send_fn=lambda *a: sent.append(a))
        self.assertEqual(len(sent), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_watcher.Actions -v`
Expected: FAIL — `module 'watcher_actions' has no attribute 'send_macos_notification'` / `notify_run`.

- [ ] **Step 3: Implement in `watcher_actions.py`**

Add `import subprocess` to the imports at the top of `watcher_actions.py` (it currently imports `os`, `sys`, `publish_video`). Replace the `run_notify` stub function with:

```python
def send_macos_notification(title, message, run_fn=subprocess.run) -> None:
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    run_fn(["osascript", "-e", script], capture_output=True, text=True)


def notify_run(result, notify_cfg, message, send_fn=send_macos_notification) -> dict:
    """Run-level notifier driven by the run summary. Returns {notified: bool, ...}."""
    if not notify_cfg.get("enabled"):
        return {"notified": False, "reason": "disabled"}
    outcomes = result.get("outcomes", [])
    published = sum(1 for o in outcomes if o.get("ok"))
    failed = len(outcomes) - published
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

Then remove `"notify": run_notify,` from the `ACTIONS` dict (leave `mytv` and `summarize`):

```python
ACTIONS = {
    "mytv": run_mytv,
    "summarize": run_summarize,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_watcher -v`
Expected: PASS (all watcher tests). The only stale reference to the removed `run_notify` was `test_stubs_return_skipped`, updated in Step 1. Confirm `grep -n run_notify test_watcher.py` returns nothing.

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher_actions.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: macOS notify (send_macos_notification + run-level notify_run)"
```

---

### Task 5: Wire notify into config + `main()`

**Files:**
- Modify: `skills/publish-video/scripts/watcher.py` (`DEFAULT_CONFIG`, `build_deps`, `run_once`, `main`)
- Modify: `skills/publish-video/scripts/watcher.toml` (move notify to top-level `[notify]`)
- Modify: `skills/publish-video/scripts/watcher.example.toml` (same)
- Test: `skills/publish-video/scripts/test_watcher.py` (`Config`, `Orchestrate`/`Cli`)

This task introduces a small `run_once(cfg, deps, log)` helper in `watcher.py` (tick + summary + notify), so both `main()` branches share one tested path — and so we can test the wiring with dependency injection (the file's style), not `mock.patch`.

- [ ] **Step 1: Write the failing tests**

Add to `test_watcher.py`. First, a config-default test in the `Config` class (the module alias is `w`):

```python
    def test_default_config_has_notify_block(self):
        cfg = w.parse_config("")  # empty file -> all defaults
        self.assertEqual(cfg["notify"]["enabled"], False)
        self.assertEqual(cfg["notify"]["trigger"], "activity")
        self.assertNotIn("notify", [a.get("name") for a in cfg["actions"]])
```

Then, in the `Orchestrate` class, a `run_once` test using the existing `_base_deps` DI fixture (no `mock`). It fakes `list_entries` to yield one fresh item (which the fake `publish` in `_base_deps` succeeds on), and a fake `notify` to capture the call:

```python
    def test_run_once_logs_summary_and_notifies(self):
        cfg = w.parse_config('')
        cfg["platforms"] = {"youtube": {"source": "watch_later"}}
        cfg["notify"] = {"enabled": True, "trigger": "activity", "title": "T"}
        notified = []
        deps = _base_deps({
            "list_entries": lambda *a, **k: [
                {"platform": "youtube", "id": "v1", "url": "u1", "title": "t"}],
            "notify": lambda result, ncfg, message, **kw: notified.append((ncfg, message)),
        })
        msgs = []
        w.run_once(cfg, "/p.py", deps, log=msgs.append)
        self.assertIn("run done: 1 published, 0 failed", msgs)
        self.assertEqual(len(notified), 1)
        self.assertEqual(notified[0][1], "1 published, 0 failed")  # prefix stripped

    def test_run_once_notify_failure_does_not_raise(self):
        cfg = w.parse_config('')
        cfg["platforms"] = {"youtube": {"source": "watch_later"}}
        cfg["notify"] = {"enabled": True, "trigger": "always"}
        def boom(*a, **k):
            raise RuntimeError("osascript missing")
        deps = _base_deps({"list_entries": lambda *a, **k: [], "notify": boom})
        msgs = []
        w.run_once(cfg, "/p.py", deps, log=msgs.append)  # must not raise
        self.assertTrue(any("notify failed" in m for m in msgs))
```

Also add `"notify"` to the key list asserted in `Cli.test_build_deps_has_real_callables` so it covers the new dep:

```python
        for key in ("list_entries", "publish", "run_actions", "load_state",
                    "save_state", "new_entries", "entry_key", "notify"):
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_watcher -v`
Expected: `test_default_config_has_notify_block` FAILS (`KeyError: 'notify'`), and `test_run_once_logs_summary_and_notifies` / `test_run_once_notify_failure_does_not_raise` FAIL (`module 'watcher' has no attribute 'run_once'`).

- [ ] **Step 3: Update `DEFAULT_CONFIG` and `build_deps`**

In `watcher.py`, the `DEFAULT_CONFIG` dict currently ends with:

```python
    "actions": [{"name": "mytv", "enabled": False, "channel": 0}],
}
```

Change it to add a `notify` block (and keep `actions` as-is — note the default `actions` never had a `notify` entry, so nothing to remove there):

```python
    "actions": [{"name": "mytv", "enabled": False, "channel": 0}],
    "notify": {"enabled": False, "trigger": "activity", "title": "publish-video watcher"},
}
```

In `build_deps()`, add the notify entry to the returned dict:

```python
def build_deps() -> dict:
    return {
        "list_entries": watcher_sources.list_entries,
        "publish": run_publish,
        "run_actions": watcher_actions.run_actions,
        "load_state": watcher_state.load_state,
        "save_state": watcher_state.save_state,
        "new_entries": watcher_state.new_entries,
        "entry_key": watcher_state.entry_key,
        "notify": watcher_actions.notify_run,
    }
```

- [ ] **Step 4: Add `run_once()` and call it from `main()`**

Add a `run_once` helper just below `tick` in `watcher.py` (it owns tick + summary log + defensive notify, so both `main` branches share one tested path):

```python
def run_once(cfg, script_path, deps, log) -> dict:
    result = tick(cfg, script_path, deps, log)
    summary = format_summary(result)
    log(summary)
    try:  # a notifier failure must never abort the run
        deps["notify"](result, cfg["notify"], summary.removeprefix("run done: "))
    except Exception as e:
        log(f"notify failed: {e}")
    return result
```

Then replace the Task-3 `main()` body (which called `tick` + logged the summary inline) so both branches call `run_once`:

```python
    deps = build_deps()
    if args.once:
        run_once(cfg, ENGINE, deps, log)
        return
    while True:
        run_once(cfg, ENGINE, deps, log)
        log(f"sleeping {cfg['poll_interval_mins']}m")
        time.sleep(cfg["poll_interval_mins"] * 60)
```

- [ ] **Step 5: Update the TOML files**

In BOTH `watcher.toml` and `watcher.example.toml`, remove the per-video notify `[[actions]]` stub block:

```toml
[[actions]]
name = "summarize"             # stub in v1 (no-op)
enabled = false
```

(Keep that `summarize` block.) Remove any `[[actions]]` block with `name = "notify"` if present (the example template may have one). Then add a top-level `[notify]` section (place it after the `actions` blocks):

```toml
# Run-level notification (macOS Notification Center) after each poll.
[notify]
enabled = false                # set true to get a desktop notification
trigger = "activity"           # activity (published>0 or failed>0 or listing error) | failure | always
title = "publish-video watcher"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m unittest test_publish_video test_watcher -v`
Expected: PASS (all tests). Also run `python3 -c "import tomllib; tomllib.load(open('watcher.toml','rb')); tomllib.load(open('watcher.example.toml','rb'))"` to confirm both TOML files parse.

- [ ] **Step 7: Commit**

```bash
git add skills/publish-video/scripts/watcher.py skills/publish-video/scripts/watcher.toml skills/publish-video/scripts/watcher.example.toml skills/publish-video/scripts/test_watcher.py
git commit -m "feat: wire run-level notify via [notify] config + main()"
```

---

### Task 6: Log rotation in the wrapper + repo template + docs

**Files:**
- Modify (outside repo): `~/.publish-video-watcher/run-watcher.sh`
- Create: `skills/publish-video/scripts/run-watcher.example.sh` (documented template in the repo)
- Modify: `skills/publish-video/REFERENCE.md` (Scheduling section: rotation + notify)

This task has no unit test (it is a bash wrapper outside the repo). Verification is manual.

- [ ] **Step 1: Add copytruncate rotation to the live wrapper**

Edit `~/.publish-video-watcher/run-watcher.sh`. Immediately after the `set -euo pipefail` line and before the run header `echo`, insert:

```bash
# Rotate the log (copytruncate) before writing, so it never grows unbounded.
# Truncating in place keeps launchd's already-open stdout/stderr fd valid.
LOG="$HOME/.publish-video-watcher/watcher.log"
MAX_BYTES=$((5 * 1024 * 1024))
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt "$MAX_BYTES" ]; then
    cp "$LOG" "$LOG.1" 2>/dev/null && : > "$LOG" || true
fi
```

- [ ] **Step 2: Create the repo template**

Create `skills/publish-video/scripts/run-watcher.example.sh` with the full wrapper (placeholders for the repo path), including the rotation block:

```bash
#!/bin/bash
# Template launchd wrapper for the publish-video watcher. Copy to
# ~/.publish-video-watcher/run-watcher.sh, edit REPO, then load the LaunchAgent.
# launchd gives a minimal PATH and no shell profile, so set everything explicitly.
set -euo pipefail

REPO="/absolute/path/to/publish-video-plugin"   # <-- edit this
SCRIPTS="$REPO/skills/publish-video/scripts"

# Rotate the log (copytruncate) before writing so it never grows unbounded.
# Truncating in place keeps launchd's already-open stdout/stderr fd valid.
LOG="$HOME/.publish-video-watcher/watcher.log"
MAX_BYTES=$((5 * 1024 * 1024))
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt "$MAX_BYTES" ]; then
    cp "$LOG" "$LOG.1" 2>/dev/null && : > "$LOG" || true
fi

# Homebrew bin first so yt-dlp / python3 / ffmpeg / ffprobe resolve under launchd.
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# S3 / MyTV credentials + config (PUBLISH_VIDEO_*, AWS_*, MYTV_*).
set -a
# shellcheck disable=SC1090
source "$REPO/.env"
set +a

cd "$SCRIPTS"
echo "===== watcher run $(date '+%Y-%m-%d %H:%M:%S') ====="
exec python3 watcher.py --once --config "$SCRIPTS/watcher.toml"
```

- [ ] **Step 3: Document in REFERENCE.md**

In `skills/publish-video/REFERENCE.md`, find the `### Scheduling` section (under the watcher docs). Append:

```markdown
The agent logs to `~/.publish-video-watcher/watcher.log`. The wrapper
(`run-watcher.example.sh` in this directory is a template) rotates that log via
copytruncate when it exceeds 5 MB, keeping one previous generation (`watcher.log.1`).
Each poll ends with a one-line summary: `run done: N published, M failed`
(plus ` · K listing errors` when a platform's listing fails).

Set `[notify] enabled = true` in `watcher.toml` for a macOS Notification Center
alert after each poll. `trigger` is `activity` (published or failed > 0, or a
listing error), `failure` (only on failure), or `always`.
```

- [ ] **Step 4: Verify the wrapper still runs and rotation is wired**

Run (manually, from repo root):

```bash
bash -n ~/.publish-video-watcher/run-watcher.sh && echo "wrapper syntax OK"
bash -n skills/publish-video/scripts/run-watcher.example.sh && echo "template syntax OK"
```

Expected: both print OK.

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/run-watcher.example.sh skills/publish-video/REFERENCE.md
git commit -m "feat: log rotation in wrapper + repo template + docs"
```

---

### Task 7: End-to-end verification under launchd (the notify risk)

**Files:** none (verification only).

The spec flags that a launchd `Background` agent may be denied Notification Center access. Verify empirically.

- [ ] **Step 1: Enable notify in the live config**

In `~/Workspace/playground/publish-video-plugin/skills/publish-video/scripts/watcher.toml`, set `[notify] enabled = true`.

- [ ] **Step 2: Trigger a run via launchd and watch the log**

```bash
launchctl kickstart -k gui/$(id -u)/se.eyevinn.publish-video-watcher
# wait a few seconds, then:
tail -20 ~/.publish-video-watcher/watcher.log
```

Expected: the log ends with a `run done: …` summary line. (MyYoutube is empty and MyBilibili items are already in state, so this will be `run done: 0 published, 0 failed` — an idle run, which under the `activity` trigger sends NO notification.)

- [ ] **Step 3: Force a notification to test Notification Center access from launchd**

Because an idle run won't notify, test the notification path directly in the launchd context. Temporarily set `[notify] trigger = "always"` in `watcher.toml`, kickstart again, and confirm a desktop notification appears:

```bash
launchctl kickstart -k gui/$(id -u)/se.eyevinn.publish-video-watcher
```

Expected: a macOS notification titled "publish-video watcher" appears. If it does NOT appear, that confirms the `Background` agent is denied notifications — record this and report; the fallback (per spec) is `terminal-notifier` or relaxing `ProcessType`. Either way, revert `trigger` to `activity` afterward.

- [ ] **Step 4: Restore config**

Set `[notify] trigger = "activity"` again (leave `enabled` per the user's preference — ask if unsure). Confirm:

```bash
python3 -c "import tomllib; print(tomllib.load(open('skills/publish-video/scripts/watcher.toml','rb'))['notify'])"
```

- [ ] **Step 5: Report**

Report the empirical result: does the launchd-context macOS notification work? This determines whether the notify feature is usable as-is or needs the fallback.

---

## Notes for the implementer

- All Python tests use stdlib `unittest` and dependency injection — never make real network calls, subprocesses, or notifications in tests; inject fakes (`run_fn`, `send_fn`, `deps`).
- Run the FULL suite (`python3 -m unittest test_publish_video test_watcher`) before each commit; the target is 104 passing tests before Task 1, growing as tasks add tests.
- The wrapper and LaunchAgent live OUTSIDE the repo (machine-specific absolute paths). Only the `run-watcher.example.sh` template and docs go in git.
- The watcher's `cfg["notify"]` is always present because `DEFAULT_CONFIG` provides it and `parse_config` shallow-merges (a user TOML without `[notify]` still gets the default block).
