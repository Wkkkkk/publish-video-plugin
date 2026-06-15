# Saved-video → publish watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone watcher that polls a configured saved-video source (Watch Later by default, or any playlist/folder URL) on YouTube + Bilibili, publishes each new item via the existing `publish_video.py` engine, and runs a config-driven list of post-publish actions.

**Architecture:** A new layer of small, dependency-injected Python modules living beside the engine in `skills/publish-video/scripts/`. The engine is reused **unchanged** by shelling out to it and parsing its JSON envelope. Source listing uses `yt-dlp --flat-playlist` (list-only, no download). Dedup lives in a local JSON state file; the source is never modified. Post-publish actions are a registry (`mytv` wired, `summarize`/`notify` stubbed) so adding one is a new function + a config line.

**Tech Stack:** Python 3.11+ stdlib only (`argparse`, `subprocess`, `json`, `tomllib`, `time`, `os`, `shutil`), `yt-dlp` (listing + site downloads), the existing engine (`publish_video.py`, which itself needs `boto3`/`ffprobe`). Config is TOML (parsed with stdlib `tomllib` — chosen over the spec's YAML sketch to avoid adding a PyYAML dependency; same shape, TOML syntax). Tests: `unittest`, mirroring `test_publish_video.py`.

**Conventions:**
- Follow the engine's style: pure functions with **injected dependencies** (run/read/write functions passed as params) so tests need no network, subprocess, or filesystem.
- All new code and tests live in `skills/publish-video/scripts/`. Run tests from that directory (so `import publish_video` and the new modules resolve, exactly like the existing suite).
- One test file: `test_watcher.py`, one `unittest.TestCase` class per module.
- Per the repo convention, **every commit message ends with the trailer**:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
  (Omitted from the short commit commands below for brevity — add it on each commit.)
- Work happens on branch `watch-later-watcher` (already created).

---

## File Structure

All paths under `skills/publish-video/scripts/`:

- Create `watcher_state.py` — dedup state: `entry_key`, `load_state`, `save_state`, `new_entries`.
- Create `watcher_sources.py` — source listing: `source_to_url`, `build_list_cmd`, `parse_listing`, `list_entries`. Knows the Watch Later URLs.
- Create `watcher_actions.py` — post-publish action registry: `run_mytv` (reuses `publish_video.register_item`/`build_payload`), `run_summarize`/`run_notify` stubs, `enabled_actions`, `run_actions` (per-action failure isolation).
- Create `watcher.py` — config (`parse_config`, `validate_config`, `load_config`), publish invocation (`build_publish_cmd`, `run_publish`, `first_result`, `make_result`), orchestration (`process_entry`, `tick`), CLI (`build_deps`, `main`).
- Create `test_watcher.py` — `unittest` suite for all four modules.
- Create `watcher.example.toml` — committed config template the user copies to `watcher.toml`.
- Modify `.gitignore` — ignore the user's real `watcher.toml` and the state file.
- Modify `skills/publish-video/REFERENCE.md` and `SKILL.md` — document the watcher.

---

## Task 1: State store (`watcher_state.py`)

**Files:**
- Create: `skills/publish-video/scripts/watcher_state.py`
- Test: `skills/publish-video/scripts/test_watcher.py`

- [ ] **Step 1: Write the failing tests**

Create `test_watcher.py` with:

```python
import json
import os
import tempfile
import unittest

import watcher_state as st


class State(unittest.TestCase):
    def test_entry_key(self):
        self.assertEqual(
            st.entry_key({"platform": "youtube", "id": "abc"}), "youtube:abc"
        )

    def test_new_entries_filters_seen(self):
        entries = [
            {"platform": "youtube", "id": "a"},
            {"platform": "youtube", "id": "b"},
        ]
        seen = {"youtube:a"}
        self.assertEqual(st.new_entries(entries, seen), [entries[1]])

    def test_load_state_missing_file_is_empty(self):
        self.assertEqual(st.load_state("/no/such/file.json"), set())

    def test_save_then_load_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "state.json")  # nested dir must be created
            st.save_state(path, {"youtube:a", "bilibili:b"})
            self.assertTrue(os.path.exists(path))
            self.assertEqual(st.load_state(path), {"youtube:a", "bilibili:b"})
            with open(path) as f:
                self.assertEqual(json.load(f), ["bilibili:b", "youtube:a"])  # sorted


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher.State -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'watcher_state'`

- [ ] **Step 3: Write the implementation**

Create `watcher_state.py`:

```python
"""Dedup state for the watcher: which {platform}:{id} items were already handled.
The saved-video source is never modified; this file is the only record of progress."""
from __future__ import annotations

import json
import os


def entry_key(entry: dict) -> str:
    return f"{entry['platform']}:{entry['id']}"


def load_state(path: str) -> set:
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return set(json.load(f))


def save_state(path: str, keys) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(sorted(keys), f, indent=2)


def new_entries(entries, seen) -> list:
    return [e for e in entries if entry_key(e) not in seen]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher.State -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher_state.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: watcher dedup state store"
```

---

## Task 2: Source listing (`watcher_sources.py`)

**Files:**
- Create: `skills/publish-video/scripts/watcher_sources.py`
- Test: `skills/publish-video/scripts/test_watcher.py` (add a class)

- [ ] **Step 1: Write the failing tests**

Append to `test_watcher.py` (before the `if __name__` block) and add `import watcher_sources as src` at the top:

```python
class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Sources(unittest.TestCase):
    def test_source_to_url_watch_later(self):
        self.assertEqual(
            src.source_to_url("youtube", "watch_later"),
            "https://www.youtube.com/playlist?list=WL",
        )
        self.assertEqual(
            src.source_to_url("bilibili", "watch_later"),
            src.WATCH_LATER["bilibili"],
        )

    def test_source_to_url_passthrough_url(self):
        url = "https://www.youtube.com/playlist?list=PL123"
        self.assertEqual(src.source_to_url("youtube", url), url)

    def test_source_to_url_rejects_bare_id(self):
        with self.assertRaises(ValueError):
            src.source_to_url("youtube", "PL123")

    def test_build_list_cmd_with_cookies(self):
        cmd = src.build_list_cmd("URL", "chrome")
        self.assertEqual(cmd[0], "yt-dlp")
        self.assertIn("--flat-playlist", cmd)
        self.assertIn("--cookies-from-browser", cmd)
        self.assertIn("chrome", cmd)
        self.assertEqual(cmd[-1], "URL")  # url after the "--" guard

    def test_build_list_cmd_without_cookies(self):
        cmd = src.build_list_cmd("URL", None)
        self.assertNotIn("--cookies-from-browser", cmd)

    def test_parse_listing(self):
        stdout = "id1\thttps://x/1\tTitle One\n\nid2\thttps://x/2\tTitle Two\n"
        got = src.parse_listing("youtube", stdout)
        self.assertEqual(got, [
            {"platform": "youtube", "id": "id1", "url": "https://x/1", "title": "Title One"},
            {"platform": "youtube", "id": "id2", "url": "https://x/2", "title": "Title Two"},
        ])

    def test_parse_listing_tolerates_missing_title(self):
        got = src.parse_listing("youtube", "id1\thttps://x/1\n")
        self.assertEqual(got[0]["title"], "")

    def test_list_entries_runs_and_parses(self):
        calls = {}

        def fake_run(cmd, capture_output, text):
            calls["cmd"] = cmd
            return FakeProc(stdout="id1\thttps://x/1\tT\n")

        got = src.list_entries("youtube", "watch_later", "chrome", run_fn=fake_run)
        self.assertEqual(got[0]["id"], "id1")
        self.assertEqual(calls["cmd"][-1], "https://www.youtube.com/playlist?list=WL")

    def test_list_entries_raises_on_failure(self):
        def fake_run(cmd, capture_output, text):
            return FakeProc(returncode=1, stderr="boom")

        with self.assertRaises(RuntimeError):
            src.list_entries("youtube", "watch_later", "chrome", run_fn=fake_run)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher.Sources -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'watcher_sources'`

- [ ] **Step 3: Write the implementation**

Create `watcher_sources.py`:

```python
"""List entries from a saved-video source (Watch Later or a playlist/folder URL)
via `yt-dlp --flat-playlist`. List-only — never downloads."""
from __future__ import annotations

import subprocess
import sys

# yt-dlp playlist URLs for each platform's Watch Later list. Verify against your
# installed yt-dlp once with real cookies (see plan Task 2, optional Step 6) —
# Bilibili's watchlater extractor URL has changed across yt-dlp versions.
WATCH_LATER = {
    "youtube": "https://www.youtube.com/playlist?list=WL",
    "bilibili": "https://www.bilibili.com/watchlater/#/list",
}

# Tab-separated so titles (which may contain spaces) survive a simple split.
PRINT_TEMPLATE = "%(id)s\t%(url)s\t%(title)s"


def source_to_url(platform: str, source: str) -> str:
    if source == "watch_later":
        try:
            return WATCH_LATER[platform]
        except KeyError:
            raise ValueError(f"no Watch Later URL known for platform: {platform}")
    if source.startswith("http://") or source.startswith("https://"):
        return source
    raise ValueError(f"source must be 'watch_later' or a full URL, got: {source!r}")


def build_list_cmd(url: str, cookies_browser) -> list:
    cmd = ["yt-dlp", "--flat-playlist", "--print", PRINT_TEMPLATE]
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    cmd += ["--", url]
    return cmd


def parse_listing(platform: str, stdout: str) -> list:
    entries = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        vid, url = parts[0], parts[1]
        title = parts[2] if len(parts) > 2 else ""
        entries.append({"platform": platform, "id": vid, "url": url, "title": title})
    return entries


def list_entries(platform, source, cookies_browser, run_fn=subprocess.run) -> list:
    url = source_to_url(platform, source)
    cmd = build_list_cmd(url, cookies_browser)
    print("+ " + " ".join(cmd), file=sys.stderr)
    proc = run_fn(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"yt-dlp listing failed for {platform} ({url}): {proc.stderr.strip()[:300]}"
        )
    return parse_listing(platform, proc.stdout)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher.Sources -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher_sources.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: watcher source listing via yt-dlp --flat-playlist"
```

- [ ] **Step 6 (optional, real-world verification — needs your cookies):**

Confirm the Watch Later URLs actually enumerate on your machine (not a unit test):
Run: `yt-dlp --flat-playlist --print "%(id)s %(title)s" --cookies-from-browser chrome "https://www.youtube.com/playlist?list=WL"`
Expected: a list of your Watch Later video IDs/titles. Repeat for `WATCH_LATER["bilibili"]`. If Bilibili errors, find the current watchlater URL in `yt-dlp --list-extractors | grep -i bili` docs and update `WATCH_LATER["bilibili"]`, then re-commit.

---

## Task 3: Post-publish action pipeline (`watcher_actions.py`)

**Files:**
- Create: `skills/publish-video/scripts/watcher_actions.py`
- Test: `skills/publish-video/scripts/test_watcher.py` (add a class)

- [ ] **Step 1: Write the failing tests**

Add `import watcher_actions as act` at the top of `test_watcher.py`, then append:

```python
SAMPLE_RESULT = {
    "platform": "youtube", "source_id": "abc", "title": "Clip",
    "public_url": "https://b/v/x.mp4", "duration_secs": 42,
}


class Actions(unittest.TestCase):
    def test_enabled_actions_filters_and_strips(self):
        config = [
            {"name": "mytv", "enabled": True, "channel": 7},
            {"name": "summarize", "enabled": False},
        ]
        self.assertEqual(act.enabled_actions(config), [("mytv", {"channel": 7})])

    def test_run_mytv_uses_engine_helpers(self):
        captured = {}

        def fake_register(base, channel, password, payload):
            captured.update(base=base, channel=channel, password=password, payload=payload)
            return {"id": 99}

        out = act.run_mytv(
            SAMPLE_RESULT, {"channel": 7}, register_fn=fake_register,
            env={"MYTV_BASE_URL": "https://tv", "MYTV_ADMIN_PASSWORD": "pw"},
        )
        self.assertEqual(out, {"mytv_item": 99})
        self.assertEqual(captured["channel"], 7)
        self.assertEqual(captured["payload"],
                         {"title": "Clip", "url": "https://b/v/x.mp4", "duration_secs": 42})

    def test_run_mytv_errors_without_env(self):
        with self.assertRaises(RuntimeError):
            act.run_mytv(SAMPLE_RESULT, {"channel": 7},
                         register_fn=lambda *a: None, env={})

    def test_stubs_return_skipped(self):
        self.assertIn("skipped", act.run_summarize(SAMPLE_RESULT, {}))
        self.assertIn("skipped", act.run_notify(SAMPLE_RESULT, {}))

    def test_run_actions_isolates_failures(self):
        def boom(result, opts):
            raise RuntimeError("kaboom")

        def good(result, opts):
            return {"did": "ok"}

        registry = {"boom": boom, "good": good}
        config = [
            {"name": "boom", "enabled": True},
            {"name": "good", "enabled": True},
        ]
        outcomes = act.run_actions(SAMPLE_RESULT, config, registry=registry, log_fn=lambda m: None)
        self.assertEqual(outcomes[0], {"action": "boom", "ok": False, "error": "kaboom"})
        self.assertEqual(outcomes[1], {"action": "good", "ok": True, "output": {"did": "ok"}})

    def test_run_actions_unknown_action(self):
        config = [{"name": "nope", "enabled": True}]
        outcomes = act.run_actions(SAMPLE_RESULT, config, registry={}, log_fn=lambda m: None)
        self.assertFalse(outcomes[0]["ok"])
        self.assertEqual(outcomes[0]["error"], "unknown action")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher.Actions -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'watcher_actions'`

- [ ] **Step 3: Write the implementation**

Create `watcher_actions.py`:

```python
"""Post-publish actions. Each action is `run(result, opts) -> dict`, where `result`
carries {platform, source_id, title, public_url, duration_secs}. The pipeline
isolates per-action failures. Registry-based: a new action is a new function + one
ACTIONS entry + a config line."""
from __future__ import annotations

import os
import sys

import publish_video  # reuse the engine's MyTV helpers, unchanged


def run_mytv(result, opts, register_fn=publish_video.register_item, env=None) -> dict:
    env = os.environ if env is None else env
    base = env.get("MYTV_BASE_URL")
    password = env.get("MYTV_ADMIN_PASSWORD")
    if not base or not password:
        raise RuntimeError("mytv action needs MYTV_BASE_URL and MYTV_ADMIN_PASSWORD")
    channel = opts.get("channel")
    if channel is None:
        raise RuntimeError("mytv action needs a 'channel' in its config")
    payload = publish_video.build_payload(
        result["title"], result["public_url"], result["duration_secs"]
    )
    item = register_fn(base, channel, password, payload)
    item_id = item.get("id", item) if isinstance(item, dict) else item
    return {"mytv_item": item_id}


def run_summarize(result, opts, **_) -> dict:
    # Stub: summarization not implemented in v1. A real version would need the local
    # file, which the shell-out engine deletes after upload — see plan "Known limits".
    return {"skipped": "summarize not implemented"}


def run_notify(result, opts, **_) -> dict:
    # Stub: notifications not implemented in v1.
    return {"skipped": "notify not implemented"}


ACTIONS = {
    "mytv": run_mytv,
    "summarize": run_summarize,
    "notify": run_notify,
}


def enabled_actions(actions_config) -> list:
    """actions_config: ordered list of dicts like {'name': 'mytv', 'enabled': True, 'channel': 7}.
    Returns ordered [(name, opts)] for enabled entries, opts stripped of name/enabled."""
    out = []
    for a in actions_config:
        if a.get("enabled"):
            opts = {k: val for k, val in a.items() if k not in ("name", "enabled")}
            out.append((a["name"], opts))
    return out


def run_actions(result, actions_config, registry=ACTIONS, log_fn=None) -> list:
    log = log_fn or (lambda m: print(m, file=sys.stderr))
    outcomes = []
    for name, opts in enabled_actions(actions_config):
        fn = registry.get(name)
        if fn is None:
            outcomes.append({"action": name, "ok": False, "error": "unknown action"})
            log(f"action {name}: unknown, skipped")
            continue
        try:
            output = fn(result, opts)
            outcomes.append({"action": name, "ok": True, "output": output})
        except Exception as e:  # isolate per-action failure; other actions still run
            outcomes.append({"action": name, "ok": False, "error": str(e)})
            log(f"action {name} failed: {e}")
    return outcomes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher.Actions -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher_actions.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: watcher post-publish action pipeline (mytv wired, stubs)"
```

---

## Task 4: Config loading (`watcher.py`, part 1)

**Files:**
- Create: `skills/publish-video/scripts/watcher.py`
- Test: `skills/publish-video/scripts/test_watcher.py` (add a class)

- [ ] **Step 1: Write the failing tests**

Add `import watcher as w` at the top of `test_watcher.py`, then append:

```python
class Config(unittest.TestCase):
    def test_parse_config_merges_defaults(self):
        cfg = w.parse_config('poll_interval_mins = 30\n')
        self.assertEqual(cfg["poll_interval_mins"], 30)
        self.assertEqual(cfg["cookies_browser"], "chrome")  # default preserved
        self.assertIn("youtube", cfg["platforms"])           # default platforms

    def test_parse_config_overrides_platforms(self):
        toml = (
            '[platforms.youtube]\n'
            'source = "https://www.youtube.com/playlist?list=PL1"\n'
        )
        cfg = w.parse_config(toml)
        self.assertEqual(cfg["platforms"]["youtube"]["source"],
                         "https://www.youtube.com/playlist?list=PL1")

    def test_parse_config_actions_array(self):
        toml = (
            '[[actions]]\nname = "mytv"\nenabled = true\nchannel = 7\n'
            '[[actions]]\nname = "summarize"\nenabled = false\n'
        )
        cfg = w.parse_config(toml)
        self.assertEqual(cfg["actions"][0],
                         {"name": "mytv", "enabled": True, "channel": 7})

    def test_validate_rejects_unknown_platform(self):
        cfg = w.parse_config('[platforms.vimeo]\nsource = "watch_later"\n')
        with self.assertRaises(ValueError):
            w.validate_config(cfg)

    def test_validate_rejects_action_without_name(self):
        cfg = w.parse_config('[[actions]]\nenabled = true\n')
        with self.assertRaises(ValueError):
            w.validate_config(cfg)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher.Config -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'watcher'`

- [ ] **Step 3: Write the implementation**

Create `watcher.py` with this initial content:

```python
#!/usr/bin/env python3
"""Poll a saved-video source (Watch Later or a playlist/folder URL) on YouTube +
Bilibili, publish each new item via publish_video.py, then run config-driven
post-publish actions. Run with --once (single pass) or as a loop. See REFERENCE.md."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib

import watcher_actions
import watcher_sources
import watcher_state

KNOWN_PLATFORMS = ("youtube", "bilibili")

DEFAULT_CONFIG = {
    "poll_interval_mins": 60,
    "transcode": False,
    "cookies_browser": "chrome",
    "state_path": os.path.expanduser("~/.publish-video-watcher/state.json"),
    "platforms": {
        "youtube": {"source": "watch_later"},
        "bilibili": {"source": "watch_later"},
    },
    "actions": [{"name": "mytv", "enabled": False, "channel": 0}],
}


def parse_config(text: str) -> dict:
    raw = tomllib.loads(text)
    return {**DEFAULT_CONFIG, **raw}  # shallow merge: top-level keys override wholesale


def load_config(path: str) -> dict:
    with open(path) as f:
        return parse_config(f.read())


def validate_config(cfg: dict) -> None:
    for plat in cfg["platforms"]:
        if plat not in KNOWN_PLATFORMS:
            raise ValueError(f"unknown platform in config: {plat}")
    for a in cfg["actions"]:
        if "name" not in a:
            raise ValueError("each [[actions]] entry needs a name")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher.Config -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: watcher TOML config loading + validation"
```

---

## Task 5: Publish invocation + envelope parsing (`watcher.py`, part 2)

**Files:**
- Modify: `skills/publish-video/scripts/watcher.py`
- Test: `skills/publish-video/scripts/test_watcher.py` (add a class)

- [ ] **Step 1: Write the failing tests**

Append to `test_watcher.py`:

```python
class Publish(unittest.TestCase):
    def test_build_publish_cmd(self):
        cmd = w.build_publish_cmd("URL", "/path/publish_video.py", transcode=False,
                                  cookies_browser="chrome")
        self.assertEqual(cmd[:2], ["python3", "/path/publish_video.py"])
        self.assertEqual(cmd[2], "URL")
        self.assertIn("--cookies-from-browser", cmd)
        self.assertNotIn("--transcode", cmd)

    def test_build_publish_cmd_transcode(self):
        cmd = w.build_publish_cmd("URL", "/p.py", transcode=True, cookies_browser="chrome")
        self.assertIn("--transcode", cmd)

    def test_run_publish_parses_envelope(self):
        envelope = {"ok": 1, "failed": 0,
                    "results": [{"public_url": "https://b/x.mp4", "duration_secs": 5,
                                 "title": "T"}]}

        def fake_run(cmd, capture_output, text):
            return FakeProc(stdout=json.dumps(envelope))

        out = w.run_publish("URL", "/p.py", False, "chrome", run_fn=fake_run)
        self.assertEqual(out, envelope)

    def test_run_publish_raises_on_config_error(self):
        def fake_run(cmd, capture_output, text):
            return FakeProc(returncode=2, stderr="missing env")

        with self.assertRaises(RuntimeError):
            w.run_publish("URL", "/p.py", False, "chrome", run_fn=fake_run)

    def test_run_publish_raises_on_bad_json(self):
        def fake_run(cmd, capture_output, text):
            return FakeProc(stdout="not json")

        with self.assertRaises(RuntimeError):
            w.run_publish("URL", "/p.py", False, "chrome", run_fn=fake_run)

    def test_first_result(self):
        self.assertEqual(w.first_result({"results": [{"a": 1}]}), {"a": 1})
        self.assertIsNone(w.first_result({"results": []}))

    def test_make_result(self):
        entry = {"platform": "youtube", "id": "abc", "url": "u", "title": "fallback"}
        published = {"public_url": "https://b/x.mp4", "duration_secs": 9, "title": "Real"}
        r = w.make_result(entry, published)
        self.assertEqual(r, {"platform": "youtube", "source_id": "abc", "title": "Real",
                             "public_url": "https://b/x.mp4", "duration_secs": 9})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher.Publish -v`
Expected: FAIL with `AttributeError: module 'watcher' has no attribute 'build_publish_cmd'`

- [ ] **Step 3: Write the implementation**

Add to `watcher.py` (after `validate_config`):

```python
def build_publish_cmd(url, script_path, transcode, cookies_browser) -> list:
    cmd = ["python3", script_path, url, "--cookies-from-browser", cookies_browser]
    if transcode:
        cmd.append("--transcode")
    return cmd


def run_publish(url, script_path, transcode, cookies_browser, run_fn=subprocess.run) -> dict:
    cmd = build_publish_cmd(url, script_path, transcode, cookies_browser)
    proc = run_fn(cmd, capture_output=True, text=True)
    # exit 0 = all ok, 1 = item failed (envelope still printed), 2 = config/usage error.
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"publish failed (exit {proc.returncode}): {proc.stderr.strip()[:300]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"could not parse publish output: {e}")


def first_result(envelope: dict):
    results = envelope.get("results", [])
    return results[0] if results else None


def make_result(entry: dict, published: dict) -> dict:
    return {
        "platform": entry["platform"],
        "source_id": entry["id"],
        "title": published.get("title", entry.get("title", "")),
        "public_url": published["public_url"],
        "duration_secs": published.get("duration_secs", 0),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher.Publish -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: watcher publish invocation + envelope parsing"
```

---

## Task 6: Orchestration (`watcher.py`, part 3)

**Files:**
- Modify: `skills/publish-video/scripts/watcher.py`
- Test: `skills/publish-video/scripts/test_watcher.py` (add a class)

- [ ] **Step 1: Write the failing tests**

Append to `test_watcher.py`:

```python
def _base_deps(overrides):
    """Fully-faked deps for tick/process_entry — no network, subprocess, or fs."""
    deps = {
        "list_entries": lambda platform, source, cookies: [],
        "publish": lambda url, script, transcode, cookies: {
            "results": [{"public_url": "https://b/x.mp4", "duration_secs": 5, "title": "T"}]},
        "run_actions": lambda result, actions: [{"action": "mytv", "ok": True}],
        "load_state": lambda path: set(),
        "save_state": lambda path, keys: None,
        "new_entries": watcher_state.new_entries,
        "entry_key": watcher_state.entry_key,
    }
    deps.update(overrides)
    return deps


class Orchestrate(unittest.TestCase):
    def test_process_entry_success_runs_actions(self):
        entry = {"platform": "youtube", "id": "abc", "url": "u", "title": "t"}
        cfg = w.parse_config('')
        ran = {}
        deps = _base_deps({"run_actions": lambda result, actions: ran.setdefault("r", result) or []})
        out = w.process_entry(entry, cfg, "/p.py", deps, log=lambda m: None)
        self.assertTrue(out["ok"])
        self.assertEqual(ran["r"]["public_url"], "https://b/x.mp4")

    def test_process_entry_publish_error_skips_actions(self):
        entry = {"platform": "youtube", "id": "abc", "url": "u", "title": "t"}
        cfg = w.parse_config('')
        ran = {"called": False}
        deps = _base_deps({
            "publish": lambda *a: {"results": [{"error": "download failed"}]},
            "run_actions": lambda *a: ran.update(called=True) or [],
        })
        out = w.process_entry(entry, cfg, "/p.py", deps, log=lambda m: None)
        self.assertFalse(out["ok"])
        self.assertFalse(ran["called"])

    def test_tick_marks_only_successful_seen(self):
        entries = [
            {"platform": "youtube", "id": "good", "url": "u1", "title": "t"},
            {"platform": "youtube", "id": "bad", "url": "u2", "title": "t"},
        ]
        saved = {"keys": None}

        def publish(url, script, transcode, cookies):
            if url == "u2":
                return {"results": [{"error": "boom"}]}
            return {"results": [{"public_url": "https://b/x.mp4", "duration_secs": 5, "title": "T"}]}

        cfg = w.parse_config('[platforms.youtube]\nsource = "watch_later"\n')
        cfg["platforms"] = {"youtube": {"source": "watch_later"}}  # single platform
        deps = _base_deps({
            "list_entries": lambda *a: entries,
            "publish": publish,
            "save_state": lambda path, keys: saved.update(keys=set(keys)),
        })
        w.tick(cfg, "/p.py", deps, log=lambda m: None)
        self.assertEqual(saved["keys"], {"youtube:good"})  # bad not recorded → retried next tick

    def test_tick_isolates_listing_failure(self):
        cfg = w.parse_config('')
        cfg["platforms"] = {"youtube": {"source": "watch_later"},
                            "bilibili": {"source": "watch_later"}}
        published = {"count": 0}

        def list_entries(platform, source, cookies):
            if platform == "youtube":
                raise RuntimeError("yt listing down")
            return [{"platform": "bilibili", "id": "b1", "url": "u", "title": "t"}]

        def publish(*a):
            published["count"] += 1
            return {"results": [{"public_url": "https://b/x.mp4", "duration_secs": 1, "title": "T"}]}

        deps = _base_deps({"list_entries": list_entries, "publish": publish})
        w.tick(cfg, "/p.py", deps, log=lambda m: None)
        self.assertEqual(published["count"], 1)  # bilibili still processed despite youtube failing
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher.Orchestrate -v`
Expected: FAIL with `AttributeError: module 'watcher' has no attribute 'process_entry'`

- [ ] **Step 3: Write the implementation**

Add to `watcher.py` (after `make_result`):

```python
def process_entry(entry, cfg, script_path, deps, log) -> dict:
    envelope = deps["publish"](entry["url"], script_path, cfg["transcode"], cfg["cookies_browser"])
    published = first_result(envelope)
    if not published or "error" in published:
        msg = published.get("error", "no result") if published else "no result"
        log(f"publish failed for {entry['url']}: {msg}")
        return {"entry": entry, "ok": False, "error": msg}
    result = make_result(entry, published)
    outcomes = deps["run_actions"](result, cfg["actions"])
    return {"entry": entry, "ok": True, "result": result, "actions": outcomes}


def tick(cfg, script_path, deps, log) -> list:
    seen = deps["load_state"](cfg["state_path"])
    handled = []
    for platform, pconf in cfg["platforms"].items():
        try:
            entries = deps["list_entries"](platform, pconf["source"], cfg["cookies_browser"])
        except Exception as e:  # one platform's listing failing must not stop the others
            log(f"listing {platform} failed: {e}")
            continue
        fresh = deps["new_entries"](entries, seen)
        log(f"{platform}: {len(entries)} listed, {len(fresh)} new")
        for entry in fresh:
            outcome = process_entry(entry, cfg, script_path, deps, log)
            handled.append(outcome)
            if outcome["ok"]:
                seen.add(deps["entry_key"](entry))
                deps["save_state"](cfg["state_path"], seen)  # persist after each success (crash-safe)
    return handled
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher.Orchestrate -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: watcher orchestration (per-tick poll → publish → actions)"
```

---

## Task 7: CLI (`watcher.py`, part 4)

**Files:**
- Modify: `skills/publish-video/scripts/watcher.py`
- Test: `skills/publish-video/scripts/test_watcher.py` (add a class)

- [ ] **Step 1: Write the failing tests**

Append to `test_watcher.py`:

```python
class Cli(unittest.TestCase):
    def test_build_deps_has_real_callables(self):
        deps = w.build_deps()
        for key in ("list_entries", "publish", "run_actions", "load_state",
                    "save_state", "new_entries", "entry_key"):
            self.assertTrue(callable(deps[key]), key)

    def test_engine_path_points_at_publish_video(self):
        self.assertTrue(w.ENGINE.endswith("publish_video.py"))

    def test_parse_args_defaults(self):
        args = w.parse_args([])
        self.assertEqual(args.config, "watcher.toml")
        self.assertFalse(args.once)
        self.assertFalse(args.dry_run)
        self.assertIsNone(args.platform)

    def test_parse_args_flags(self):
        args = w.parse_args(["--once", "--platform", "youtube", "--config", "x.toml"])
        self.assertTrue(args.once)
        self.assertEqual(args.platform, "youtube")
        self.assertEqual(args.config, "x.toml")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher.Cli -v`
Expected: FAIL with `AttributeError: module 'watcher' has no attribute 'build_deps'`

- [ ] **Step 3: Write the implementation**

Add to `watcher.py` (after `tick`). Note `parse_args` is split out from `main` so it is unit-testable:

```python
ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "publish_video.py")


def build_deps() -> dict:
    return {
        "list_entries": watcher_sources.list_entries,
        "publish": run_publish,
        "run_actions": watcher_actions.run_actions,
        "load_state": watcher_state.load_state,
        "save_state": watcher_state.save_state,
        "new_entries": watcher_state.new_entries,
        "entry_key": watcher_state.entry_key,
    }


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Poll saved-video sources and publish new items via publish_video.py."
    )
    p.add_argument("--config", default="watcher.toml", help="path to TOML config (default: watcher.toml)")
    p.add_argument("--once", action="store_true", help="run a single pass, then exit")
    p.add_argument("--platform", choices=KNOWN_PLATFORMS, help="only poll this platform")
    p.add_argument("--dry-run", action="store_true", help="list new items per platform; do not publish")
    return p.parse_args(argv)


def main():
    args = parse_args()
    log = lambda m: print(m, file=sys.stderr)
    try:
        cfg = load_config(args.config)
        validate_config(cfg)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    if args.platform:
        cfg["platforms"] = {args.platform: cfg["platforms"][args.platform]}
    if shutil.which("yt-dlp") is None:
        print("error: yt-dlp not found on PATH (needed to list sources)", file=sys.stderr)
        sys.exit(2)

    if args.dry_run:
        seen = watcher_state.load_state(cfg["state_path"])
        for platform, pconf in cfg["platforms"].items():
            entries = watcher_sources.list_entries(platform, pconf["source"], cfg["cookies_browser"])
            fresh = watcher_state.new_entries(entries, seen)
            print(json.dumps({"platform": platform, "new": fresh}, indent=2))
        return

    deps = build_deps()
    if args.once:
        tick(cfg, ENGINE, deps, log)
        return
    while True:
        tick(cfg, ENGINE, deps, log)
        log(f"sleeping {cfg['poll_interval_mins']}m")
        time.sleep(cfg["poll_interval_mins"] * 60)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher.Cli -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the FULL suite (watcher + engine) to confirm nothing regressed**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher test_publish_video -v`
Expected: PASS (all watcher classes + all existing engine tests)

- [ ] **Step 6: Commit**

```bash
git add skills/publish-video/scripts/watcher.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: watcher CLI (--once / --dry-run / --platform / loop)"
```

---

## Task 8: Config template, gitignore, and docs

**Files:**
- Create: `skills/publish-video/scripts/watcher.example.toml`
- Modify: `.gitignore`
- Modify: `skills/publish-video/REFERENCE.md`
- Modify: `skills/publish-video/SKILL.md`

- [ ] **Step 1: Create the config template**

Create `skills/publish-video/scripts/watcher.example.toml`:

```toml
# Copy to watcher.toml and edit. Secrets do NOT go here — S3/MyTV creds come from
# the environment (.env), cookies come from your browser (cookies_browser below).

poll_interval_mins = 60        # loop mode only; ignored with --once
transcode = false              # re-encode non-H.264/AAC inputs before upload
cookies_browser = "chrome"     # browser yt-dlp reads cookies from (Watch Later is private)
state_path = "~/.publish-video-watcher/state.json"  # dedup record; never your source

[platforms.youtube]
source = "watch_later"         # or a full playlist/folder URL, e.g.
                               # "https://www.youtube.com/playlist?list=PLxxxx"

[platforms.bilibili]
source = "watch_later"         # or a full favorites-folder URL

# Post-publish actions run in order, for every newly-published video.
# Add an action = add a function in watcher_actions.py + a block here.
[[actions]]
name = "mytv"
enabled = false                # set true + a real channel to auto-register into MyTV
channel = 7

[[actions]]
name = "summarize"             # stub in v1 (no-op)
enabled = false
```

Note: `state_path` with a leading `~` — confirm `parse_config`/`load_state` expand it. The DEFAULT_CONFIG value is already expanded via `os.path.expanduser`, but a user-supplied `state_path` from TOML is literal. Add expansion in `tick`/`main` where state_path is used, OR expand once in `validate_config`. Implement: in `validate_config`, add `cfg["state_path"] = os.path.expanduser(cfg["state_path"])`. Add a test in the `Config` class:

```python
    def test_validate_expands_state_path(self):
        cfg = w.parse_config('state_path = "~/foo/state.json"')
        w.validate_config(cfg)
        self.assertFalse(cfg["state_path"].startswith("~"))
```

Run that test, watch it fail, add the one line to `validate_config`, watch it pass.

- [ ] **Step 2: Update `.gitignore`**

Read `.gitignore`, then add (the example template stays tracked; the user's real config and state do not):

```
watcher.toml
.publish-video-watcher/
```

- [ ] **Step 3: Document in `REFERENCE.md`**

Add a "Watch Later watcher" section to `skills/publish-video/REFERENCE.md` covering: what it does, the `watcher.example.toml` → `watcher.toml` copy step, the env it relies on (same `PUBLISH_VIDEO_*`/`MYTV_*` as the engine, plus browser cookies), the CLI (`--once`, `--dry-run`, `--platform`, loop mode), the action registry + how to add one, and the v1 limitations (read-only source, full-URL sources only — no bare IDs, `summarize`/`notify` are stubs).

- [ ] **Step 4: Document in `SKILL.md`**

Add a short "Auto-publish from Watch Later" subsection pointing at the watcher and `REFERENCE.md`, with the two invocations:

```bash
# one-off / manual
python3 ${CLAUDE_PLUGIN_ROOT}/skills/publish-video/scripts/watcher.py --once --config ~/watcher.toml
# preview what would be published, no upload
python3 ${CLAUDE_PLUGIN_ROOT}/skills/publish-video/scripts/watcher.py --dry-run --config ~/watcher.toml
```

- [ ] **Step 5: Run the full suite once more**

Run: `cd skills/publish-video/scripts && python3 -m unittest test_watcher test_publish_video -v`
Expected: PASS (all tests, including the new state_path-expansion test)

- [ ] **Step 6: Commit**

```bash
git add skills/publish-video/scripts/watcher.example.toml .gitignore \
        skills/publish-video/REFERENCE.md skills/publish-video/SKILL.md \
        skills/publish-video/scripts/watcher.py skills/publish-video/scripts/test_watcher.py
git commit -m "docs: config template, gitignore, watcher docs"
```

---

## Scheduling (post-implementation, not a code task)

Once the watcher runs green via `--once`, wire automatic polling:

- **Recommended:** a local Claude routine (`/schedule`) whose job is to run
  `python3 .../watcher.py --once --config ~/watcher.toml` every `poll_interval_mins`.
  **Confirm the routine fires on this machine** (so browser cookies + `~/.aws` +
  `PUBLISH_VIDEO_*`/`MYTV_*` env resolve). If it runs as a cloud agent without your
  secrets, fall back to:
- **launchd/cron:** a timer running the same `--once` command. The script is standalone,
  so this needs no Claude.

Manual runs are always just the `--once` command by hand.

---

## Known limitations (v1, by design)

- **Source is read-only** — items are never removed from Watch Later; dedup is the state file.
- **Full-URL or `watch_later` sources only** — bare playlist/folder IDs are not resolved in v1 (use a full URL).
- **`summarize`/`notify` are no-op stubs.** A real `summarize` needs the local video file, but the shell-out engine deletes its workdir after upload — enabling it later means either an engine `--keep` flag or having the action re-download. Out of scope now; the action contract already carries enough to add it without touching the pipeline.
- **Failed publishes retry** next tick (not recorded in state). A permanently-failing item retries every tick until it succeeds or you remove it from the source.

---

## Self-Review

Checked the plan against the spec (`2026-06-15-saved-video-publish-watcher-design.md`):

- **Source adapters / list-only / configurable source** → Task 2 (`source_to_url` handles `watch_later` + URL; `list_entries` uses `--flat-playlist`). ✔ (Spec's "or an ID" narrowed to "or a URL" — recorded under Known limitations.)
- **State store / read-only source / dedup** → Task 1. ✔
- **Engine reused unchanged** → Tasks 5–6 shell out to `publish_video.py`. ✔
- **Action pipeline, config-driven, mytv wired + stubs, per-action isolation** → Task 3. ✔
- **Orchestrator, failed-publish-not-marked-seen, per-platform isolation** → Task 6. ✔
- **Config (per-platform source, actions, transcode)** → Task 4 + template Task 8. ✔ (YAML→TOML noted in header.)
- **Scheduling (manual + local Claude routine), error handling, testing** → CLI Task 7, Scheduling + Known-limits sections. ✔
- **Placeholder scan:** no TBD/TODO; every code step has complete code. ✔
- **Type consistency:** the `entry` dict (`platform/id/url/title`), the `result` dict (`platform/source_id/title/public_url/duration_secs`), and the action `outcome` dict (`action/ok/output|error`) are used identically across Tasks 1–7. `run_publish`/`first_result`/`make_result` signatures match their call sites in `process_entry`. ✔
