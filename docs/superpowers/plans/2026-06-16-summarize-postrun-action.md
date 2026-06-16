# `summarize` Post-Run Action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a run-level `summarize` post-run action that feeds each newly-published video's R2 `public_url` to the external `video-summarizer` CLI, writing one markdown analysis per video and sending one summary notification per run.

**Architecture:** A new `summarize_action(run_context, opts, log, env, run_fn, send_fn)` in `watcher_actions.py`, registered in `POST_RUN_ACTIONS`, modeled exactly on the existing `mytv_action` (iterate successful outcomes, isolate per-item failures). It shells out to `video-summarizer`; success is detected by the CLI printing a `.md` path to stdout. The dead per-video `run_summarize` stub is retired (this feature is its real implementation, at the run level). Cheap pass only — `--visual` is opt-in via config and off by default.

**Tech Stack:** Python 3 stdlib (`unittest`, `subprocess`, dependency injection), `tomllib`.

**Spec:** `docs/superpowers/specs/2026-06-16-summarize-postrun-action-design.md`

**Test commands** (from `skills/publish-video/scripts/`):
- Engine: `python3 -m unittest test_publish_video -v`
- Watcher: `python3 -m unittest test_watcher -v`
- Both: `python3 -m unittest test_publish_video test_watcher`

**Module import aliases in tests:** `import watcher as w`, `import watcher_actions as act`, `import publish_video as v`. The `Actions` test class already has a `_mytv_ctx()` helper returning a run_context with two successful items (`Y1`/youtube/`https://b/y1.mp4`, `B1`/bilibili/`https://b/b1.mp4`) and one failed outcome — reuse it for summarize tests.

**Baseline:** 133 tests pass at the start. Net count grows (Task 1 adds 8, Task 2 removes 1).

Commit trailer (exact) for every commit: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: `summarize_action` + register in `POST_RUN_ACTIONS`

**Files:**
- Modify: `skills/publish-video/scripts/watcher_actions.py` (add `summarize_action`; add it to `POST_RUN_ACTIONS`)
- Modify: `skills/publish-video/scripts/test_watcher.py` (`Actions` class; add a `_Proc` fake near `SAMPLE_RESULT`, ~line 168)

- [ ] **Step 1: Add the `_Proc` fake and the failing tests**

In `test_watcher.py`, immediately AFTER the `SAMPLE_RESULT = {...}` block (~line 168), add a tiny fake for `subprocess.run` results:

```python
class _Proc:  # minimal stand-in for subprocess.CompletedProcess
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode
```

Then add these tests to the `Actions` class (anywhere inside it, e.g. after `test_run_post_run_unknown_action`):

```python
    def test_summarize_action_no_items_skips(self):
        def boom(*a, **k): raise AssertionError("should not run")
        out = act.summarize_action(
            {"outcomes": [{"ok": False}], "listing_errors": [], "summary": "s"},
            {"enabled": True}, run_fn=boom, send_fn=boom)
        self.assertIn("skipped", out)

    def test_summarize_action_runs_per_item_and_writes(self):
        calls, sent = [], []
        def fake_run(cmd, **kw):
            calls.append(cmd)
            slug = cmd[1].rsplit("/", 1)[-1].replace(".mp4", "")
            return _Proc(stdout=f"/out/{slug}.md\n", returncode=0)
        out = act.summarize_action(
            self._mytv_ctx(), {"enabled": True, "out": "/out", "notify": True},
            run_fn=fake_run, send_fn=lambda *a: sent.append(a))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], "https://b/y1.mp4")
        self.assertIn("--out", calls[0]); self.assertIn("/out", calls[0])
        self.assertEqual(out["summarized"], 2)
        self.assertEqual([a["title"] for a in out["analyses"]], ["Y1", "B1"])
        self.assertEqual(len(sent), 1)  # exactly one run-level notification

    def test_summarize_action_passes_lang_and_visual(self):
        calls = []
        act.summarize_action(
            {"outcomes": [{"ok": True, "result": {"platform": "youtube", "title": "Y",
             "public_url": "u", "duration_secs": 1}}], "listing_errors": [], "summary": "s"},
            {"enabled": True, "lang": "zh", "visual": True, "notify": False},
            run_fn=lambda cmd, **kw: calls.append(cmd) or _Proc(stdout="/o/y.md", returncode=0),
            send_fn=lambda *a: None)
        self.assertIn("--lang", calls[0]); self.assertIn("zh", calls[0])
        self.assertIn("--visual", calls[0])

    def test_summarize_action_partial_still_counts(self):
        out = act.summarize_action(
            {"outcomes": [{"ok": True, "result": {"platform": "youtube", "title": "Y",
             "public_url": "u", "duration_secs": 1}}], "listing_errors": [], "summary": "s"},
            {"enabled": True, "notify": False},
            run_fn=lambda cmd, **kw: _Proc(stdout="/o/y.md\n", stderr="warning: summary failed", returncode=1),
            send_fn=lambda *a: None)
        self.assertEqual(out["summarized"], 1)  # rc 1 but a file was written

    def test_summarize_action_skips_failure_and_isolates(self):
        def fake_run(cmd, **kw):
            if cmd[1] == "https://b/y1.mp4":
                return _Proc(stdout="", stderr="error: GEMINI_API_KEY", returncode=2)
            return _Proc(stdout="/o/b1.md\n", returncode=0)
        out = act.summarize_action(self._mytv_ctx(), {"enabled": True, "notify": False},
                                   run_fn=fake_run, send_fn=lambda *a: None)
        self.assertEqual(out["summarized"], 1)
        self.assertEqual([a["title"] for a in out["analyses"]], ["B1"])

    def test_summarize_action_command_not_found_raises(self):
        def fake_run(cmd, **kw): raise FileNotFoundError(cmd[0])
        with self.assertRaises(RuntimeError):
            act.summarize_action(self._mytv_ctx(), {"enabled": True},
                                 run_fn=fake_run, send_fn=lambda *a: None)

    def test_summarize_action_one_notification_with_titles(self):
        sent = []
        act.summarize_action(
            self._mytv_ctx(), {"enabled": True, "out": "/o", "title": "VS"},
            run_fn=lambda cmd, **kw: _Proc(stdout="/o/x.md\n", returncode=0),
            send_fn=lambda *a: sent.append(a))
        self.assertEqual(len(sent), 1)
        title, msg = sent[0]
        self.assertEqual(title, "VS")
        self.assertIn("Y1", msg); self.assertIn("B1", msg)

    def test_summarize_action_notify_disabled(self):
        sent = []
        act.summarize_action(
            self._mytv_ctx(), {"enabled": True, "notify": False},
            run_fn=lambda cmd, **kw: _Proc(stdout="/o/x.md\n", returncode=0),
            send_fn=lambda *a: sent.append(a))
        self.assertEqual(sent, [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_watcher.Actions -v 2>&1 | tail -15`
Expected: FAIL — `module 'watcher_actions' has no attribute 'summarize_action'`.

- [ ] **Step 3: Implement `summarize_action`**

In `watcher_actions.py`, add this function immediately AFTER `mytv_action` (and BEFORE the `ACTIONS`/`POST_RUN_ACTIONS` registry dicts, since the registry references it). It uses only already-imported `os`, `subprocess`, and the module-level `send_macos_notification`:

```python
def summarize_action(run_context, opts, log=None, env=None,
                     run_fn=subprocess.run, send_fn=send_macos_notification) -> dict:
    """Run-level: summarize each published video with the external `video-summarizer`
    CLI, feeding it the R2 public_url. Writes one markdown per video (the CLI prints
    its path), isolates per-item failures, and sends one summary notification per run."""
    log = log or (lambda m: None)
    env = os.environ if env is None else env
    items = [o["result"] for o in run_context.get("outcomes", []) if o.get("ok")]
    if not items:
        return {"skipped": "no items"}
    command = opts.get("command", "video-summarizer")
    out_dir = os.path.expanduser(opts.get("out", "./analyses"))
    lang = opts.get("lang") or ""
    visual = opts.get("visual", False)
    analyses = []
    for r in items:
        cmd = [command, r["public_url"], "--out", out_dir]
        if lang:
            cmd += ["--lang", lang]
        if visual:
            cmd.append("--visual")
        try:
            proc = run_fn(cmd, capture_output=True, text=True, env=env)
        except FileNotFoundError:
            raise RuntimeError(f"summarize: command not found: {command}")
        stdout = (proc.stdout or "").strip()
        path = stdout.splitlines()[-1].strip() if stdout else ""
        if path.endswith(".md"):  # CLI prints the written path; .md => a file exists
            analyses.append({"title": r["title"], "path": path})
        else:
            log(f"summarize: {r.get('title')!r} failed (exit {proc.returncode}): "
                f"{(proc.stderr or '').strip()[:200]}")
    if analyses and opts.get("notify", True):
        titles = ", ".join(a["title"] for a in analyses)
        send_fn(opts.get("title", "video-summarizer"),
                f"{len(analyses)} analyses → {out_dir}: {titles}")
    return {"summarized": len(analyses), "out": out_dir, "analyses": analyses}
```

Then add `summarize` to the `POST_RUN_ACTIONS` registry (it currently has `notify` and `mytv`):

```python
POST_RUN_ACTIONS = {
    "notify": notify_action,
    "mytv": mytv_action,
    "summarize": summarize_action,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_watcher -v 2>&1 | tail -5`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher_actions.py skills/publish-video/scripts/test_watcher.py
git commit -m "$(printf 'feat: summarize_action — run-level video-summarizer post-run action\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: Retire the dead per-video `run_summarize` stub

**Files:**
- Modify: `skills/publish-video/scripts/watcher_actions.py` (remove `run_summarize`; empty the `ACTIONS` registry)
- Modify: `skills/publish-video/scripts/watcher.py` (`DEFAULT_CONFIG` — drop the per-video summarize action)
- Modify: `skills/publish-video/scripts/test_watcher.py` (delete `test_stubs_return_skipped`)

The per-video `run_summarize` was always a no-op placeholder for this feature; `summarize_action` (Task 1) is its run-level realization. Remove the dead code.

- [ ] **Step 1: Delete the stub's test**

In `test_watcher.py`, DELETE this test (it is the only caller of `act.run_summarize`):

```python
    def test_stubs_return_skipped(self):
        self.assertIn("skipped", act.run_summarize(SAMPLE_RESULT, {}))
```

Leave `test_enabled_actions_filters_and_strips` and `test_parse_config_actions_array` unchanged — they use `"summarize"` only as generic config data and never call the function.

- [ ] **Step 2: Run to verify FAIL**

Run: `python3 -m unittest test_watcher -v 2>&1 | tail -5`
Expected: PASS (deleting a test does not break the suite). This step confirms the suite is green before the code change; proceed.

- [ ] **Step 3: Remove the stub and empty the per-video registry**

In `watcher_actions.py`, DELETE the entire `run_summarize` function:

```python
def run_summarize(result, opts, **_) -> dict:
    # Stub: summarization not implemented in v1. A real version would need the local
    # file, which the shell-out engine deletes after upload — see plan "Known limits".
    return {"skipped": "summarize not implemented"}
```

Then change the per-video `ACTIONS` registry from:

```python
ACTIONS = {
    "summarize": run_summarize,
}
```

to an empty registry (the `run_actions` machinery stays for future per-video actions):

```python
ACTIONS = {}  # no per-video actions in v1; run-level actions live in POST_RUN_ACTIONS
```

- [ ] **Step 4: Drop the per-video summarize entry from `DEFAULT_CONFIG`**

In `watcher.py`, change this line in `DEFAULT_CONFIG`:

```python
    "actions": [{"name": "summarize", "enabled": False}],
```

to an empty list (validated by `validate_config`, which only iterates it):

```python
    "actions": [],
```

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest test_publish_video test_watcher 2>&1 | tail -5`
Expected: all PASS. Confirm no dangling references: `grep -rn "run_summarize" skills/publish-video/scripts/` returns nothing.

- [ ] **Step 6: Commit**

```bash
git add skills/publish-video/scripts/watcher_actions.py skills/publish-video/scripts/watcher.py skills/publish-video/scripts/test_watcher.py
git commit -m "$(printf 'refactor: retire dead per-video summarize stub (realized as summarize_action)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 3: Wire `summarize` into config (`DEFAULT_CONFIG` + TOML)

**Files:**
- Modify: `skills/publish-video/scripts/watcher.py` (`DEFAULT_CONFIG` — add summarize to `post_run`)
- Modify: `skills/publish-video/scripts/test_watcher.py` (`Config.test_default_config_has_post_run`)
- Modify: `skills/publish-video/scripts/watcher.example.toml` (committed)
- Modify: `skills/publish-video/scripts/watcher.toml` (gitignored local; edit but not committed)

- [ ] **Step 1: Update the default-config test**

In `test_watcher.py`, REPLACE `test_default_config_has_post_run` with a version that expects the third action and asserts the summarize defaults:

```python
    def test_default_config_has_post_run(self):
        cfg = w.parse_config("")
        names = [a.get("name") for a in cfg["post_run"]]
        self.assertEqual(names, ["notify", "mytv", "summarize"])
        summarize = [a for a in cfg["post_run"] if a["name"] == "summarize"][0]
        self.assertFalse(summarize["enabled"])
        self.assertEqual(summarize["command"], "video-summarizer")
        self.assertEqual(summarize["out"], "~/video-analyses")
        self.assertFalse(summarize["visual"])
```

- [ ] **Step 2: Run to verify FAIL**

Run: `python3 -m unittest test_watcher.Config.test_default_config_has_post_run -v 2>&1 | tail -10`
Expected: FAIL — `names` is `["notify", "mytv"]`, missing `"summarize"`.

- [ ] **Step 3: Add the summarize entry to `DEFAULT_CONFIG["post_run"]`**

In `watcher.py`, the `post_run` list currently ends with the `mytv` entry. Add a third entry so the list reads:

```python
    "post_run": [
        {"name": "notify", "enabled": False, "trigger": "activity", "title": "publish-video watcher"},
        {"name": "mytv", "enabled": False, "type": "vod_on_demand", "category": "saved",
         "channels": {"youtube": "MyYoutube", "bilibili": "MyBilibili"}},
        {"name": "summarize", "enabled": False, "command": "video-summarizer",
         "out": "~/video-analyses", "lang": "", "visual": False, "notify": True},
    ],
```

- [ ] **Step 4: Run to verify PASS**

Run: `python3 -m unittest test_watcher 2>&1 | tail -5`
Expected: all PASS.

- [ ] **Step 5: Update `watcher.example.toml` (committed)**

In `watcher.example.toml`, the file currently has a per-video `[[actions]] summarize` block (lines ~21-24). REPLACE that block:

```toml
# Per-video actions run in order for each newly-published video.
[[actions]]
name = "summarize"             # stub in v1 (no-op)
enabled = false
```

with a comment noting there are no per-video actions in v1:

```toml
# Per-video actions run in order for each newly-published video. None built in for v1;
# add one via a function + an ACTIONS entry + an [[actions]] block.
```

Then, AFTER the existing `[[post_run]]` `mytv` block (end of file), APPEND the summarize block:

```toml

[[post_run]]
name = "summarize"             # analyze each published video with video-summarizer
enabled = false
command = "video-summarizer"   # absolute path recommended under launchd, e.g.
                               # /Users/you/Workspace/playground/video-summarizer/.venv/bin/video-summarizer
out = "~/video-analyses"       # where <slug>.md analyses are written
lang = ""                      # "" = auto-detect; or "zh", "en", …
visual = false                 # expensive Gemini Pro pass; run by hand instead
notify = true                  # one summary notification per run
```

- [ ] **Step 6: Mirror the change into the local `watcher.toml` (gitignored)**

Read `watcher.toml` first. Apply the same two edits (remove the per-video `summarize` `[[actions]]` block; append the `[[post_run]]` summarize block). For a live run you will likely set `enabled = true`, an absolute `command` path, and a real `out` dir — but committing is not required (the file is gitignored). Leave `enabled` per the user's preference.

- [ ] **Step 7: Full suite + TOML parse check**

```bash
python3 -m unittest test_publish_video test_watcher 2>&1 | tail -5
python3 -c "import tomllib; tomllib.load(open('watcher.toml','rb')); tomllib.load(open('watcher.example.toml','rb')); print('TOML OK')"
```
Expected: all tests PASS; `TOML OK`.

- [ ] **Step 8: Commit** (only the committed files; `watcher.toml` is gitignored and will be skipped)

```bash
git add skills/publish-video/scripts/watcher.py skills/publish-video/scripts/watcher.example.toml skills/publish-video/scripts/test_watcher.py
git commit -m "$(printf 'feat: wire summarize into [[post_run]] config (default disabled)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 4: Documentation (REFERENCE.md)

**Files:**
- Modify: `skills/publish-video/REFERENCE.md`

- [ ] **Step 1: Update the Config bullets**

In `REFERENCE.md`, find the `### Config` section. REPLACE this bullet (the per-video `actions` one, ~line 96):

```markdown
- `actions` — ordered per-video post-publish steps (run once per published video). `summarize` is a no-op stub. Add one via a function + an `ACTIONS` entry + a config block.
```

with:

```markdown
- `actions` — ordered per-video post-publish steps (run once per published video). None built in for v1. Add one via a function + an `ACTIONS` entry + an `[[actions]]` block.
```

Then REPLACE the `post_run` bullet (~line 97) to list the third built-in action:

```markdown
- `post_run` — ordered run-level actions (run once per poll, after publishing), via a parallel `[[post_run]]` registry. Built in: `notify` (macOS Notification Center), `mytv` (auto-register published videos into MyTV), and `summarize` (analyze each published video with the external `video-summarizer` CLI). Add one via a function + a `POST_RUN_ACTIONS` entry + a `[[post_run]]` block.
```

- [ ] **Step 2: Fix the stale "summarize is a stub" limitation**

In the `### Behavior & limitations (v1)` section, REPLACE this line (~line 102):

```markdown
- `summarize` is a stub; a real `summarize` needs the local file, which the engine deletes after upload.
```

with a line describing the realized action:

```markdown
- `summarize` (a `[[post_run]]` action) analyzes the **uploaded R2 URL**, not the local file (which the engine deletes after upload), so it runs at the run level rather than per-video.
```

- [ ] **Step 3: Document the summarize action in the Scheduling section**

In the `### Scheduling` section, AFTER the `mytv` paragraph (ends "...`MYTV_BASE_URL` + `MYTV_ADMIN_PASSWORD` in the environment."), APPEND a new paragraph:

```markdown
`summarize` runs the external `video-summarizer` CLI over each published video's R2 `public_url`,
writing `<out>/<slug>.md` (transcript + summary + chapters). Options: `command` (CLI path — use an
absolute venv path under launchd), `out` (output dir, default `~/video-analyses`), `lang`
(`""` = auto-detect), `visual` (off; the expensive Gemini Pro pass — run by hand instead), and
`notify` (one summary notification per run). Requires `video-summarizer` installed and
`GEMINI_API_KEY` in the environment (fold it into the watcher `.env`). A per-video failure is
logged and skipped; the run still completes.
```

- [ ] **Step 4: Verify the docs read consistently**

Run: `grep -n "summarize\|post_run\|video-analyses\|GEMINI" skills/publish-video/REFERENCE.md`
Confirm there is no remaining claim that `summarize` is a no-op stub or a per-video action, and that the new action + its env dependency are documented.

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/REFERENCE.md
git commit -m "$(printf 'docs: document summarize post-run action + GEMINI_API_KEY dependency\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 5: Live verification (controller-run, not a subagent)

**Files:** none (verification only). The controller runs this against the live `video-summarizer` install + an existing R2 MP4 after Tasks 1–4 are merge-ready.

- [ ] **Step 1: Confirm `video-summarizer` is reachable and `GEMINI_API_KEY` is set**

```bash
cd /Users/kunwu/Workspace/playground/publish-video-plugin
set -a; source .env 2>/dev/null; set +a
which video-summarizer || ls /Users/kunwu/Workspace/playground/video-summarizer/.venv/bin/video-summarizer
test -n "$GEMINI_API_KEY" && echo "GEMINI_API_KEY present" || echo "MISSING GEMINI_API_KEY"
```
Expected: a `video-summarizer` path prints, and `GEMINI_API_KEY present`.

- [ ] **Step 2: Faithful live test of `summarize_action` without re-publishing.** Build a one-item `run_context` pointing at an existing bucket MP4 and invoke `summarize_action` against the real CLI (use the absolute command path so it works regardless of PATH; `--lang en` keeps it fast on the known English clip):

```bash
cd /Users/kunwu/Workspace/playground/publish-video-plugin
set -a; source .env 2>/dev/null; set +a
cd skills/publish-video/scripts
python3 -c "
import watcher_actions as act
ctx = {'outcomes': [{'ok': True, 'result': {'platform': 'youtube', 'title': 'Me at the zoo',
       'public_url': 'https://pub-7fae8d6805af4dc6a5b2a9988274addf.r2.dev/video/youtube-20260615-jNQXAC9IVRw-Me_at_the_zoo.mp4',
       'duration_secs': 19}}], 'listing_errors': [], 'summary': 's'}
print(act.summarize_action(ctx, {'enabled': True,
      'command': '/Users/kunwu/Workspace/playground/video-summarizer/.venv/bin/video-summarizer',
      'out': '/tmp/video-analyses', 'lang': 'en', 'notify': False}, log=print))
"
```
Expected: prints `{'summarized': 1, 'out': '/tmp/video-analyses', 'analyses': [{'title': 'Me at the zoo', 'path': '/tmp/video-analyses/me-at-the-zoo.md'}]}` (slug may differ).

- [ ] **Step 3: Confirm the analysis file was written and is non-trivial**

```bash
ls -la /tmp/video-analyses/*.md
head -40 /tmp/video-analyses/*.md
```
Expected: one `.md` file containing a title, a transcript section, and a summary/chapters section.

- [ ] **Step 4: Report** the result (summarized count, output path, a one-line read of the analysis quality). Note any cleanup the user may want (the `/tmp/video-analyses` test output).

---

## Notes for the implementer

- All unit tests use stdlib `unittest` + dependency injection — never make real subprocess calls, network calls, or notifications in tests; inject `run_fn`/`send_fn`/`env` (and use the `_Proc` fake).
- Run the FULL suite (`python3 -m unittest test_publish_video test_watcher`) before each commit. Baseline at the start is 133 tests.
- `watcher.toml` and `.env` are gitignored (local). Only `watcher.example.toml`, the `.py` files, and docs are committed.
- Tasks are sequential and touch overlapping files (`watcher_actions.py`, `test_watcher.py`, `watcher.py`) — do them in order; do not parallelize implementers.
- `summarize_action` mirrors `mytv_action`: same signature shape, same per-item isolation, same `env=os.environ` default so `run_post_run` (which calls `fn(run_context, opts, log=log)`) supplies real subprocess/notification/env at runtime.
- Success detection is deliberately by **stdout ending in `.md`**, not by exit code: the CLI returns `1` for partial success but still writes the file (and prints its path), while transcript-failure (`1`, no file) and config error (`2`) print no path.
