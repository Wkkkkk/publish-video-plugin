# Parallel publishing for the watcher — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the watcher publish new items concurrently (a bounded thread pool, default 5) and download each video's fragments in parallel (yt-dlp `-N`, default 4).

**Architecture:** Each video's work runs in a `publish_video.py` subprocess, so a `ThreadPoolExecutor` in `tick` gives true parallelism (threads block on subprocess I/O; the GIL is released during `subprocess.run`). State writes are serialized with a `threading.Lock`. The engine gains one `--concurrent-fragments` flag that injects `-N` into its yt-dlp command.

**Tech Stack:** Python 3.11+ stdlib (`concurrent.futures.ThreadPoolExecutor`, `threading`), the existing engine and watcher modules under `skills/publish-video/scripts/`. Tests: `unittest`.

**Conventions:**
- Dependency-injection style (functions take injected callables) so tests need no real network/subprocess/threads-of-consequence — the pool runs fake callables.
- Run tests from `skills/publish-video/scripts/`.
- Branch: `watcher-parallel` (already checked out). Every commit ends with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` (omitted from the short commands below for brevity).
- Baseline before this plan: the full suite (`test_watcher` + `test_publish_video`) is **83 tests, green**. Each task below adds tests; expected counts are guidance.

---

## File Structure

All under `skills/publish-video/scripts/`:
- Modify `publish_video.py` — add `--concurrent-fragments` arg; thread it through `process_job → acquire → download_and_mux → build_ytdlp_cmd` to inject `-N`.
- Modify `test_publish_video.py` — engine `-N` tests.
- Modify `watcher.py` — config defaults (`concurrency`, `concurrent_fragments`) + `--concurrency` CLI; `build_publish_cmd`/`run_publish`/`process_entry` thread `concurrent_fragments`; rewrite `tick` to use a thread pool + lock.
- Modify `test_watcher.py` — config, plumbing, and parallel-tick tests; update existing fakes for the new signatures.
- Modify `watcher.example.toml`, `skills/publish-video/REFERENCE.md` — document the two knobs + `--concurrency`.

---

## Task 1: Engine `-N` support (`publish_video.py`)

**Files:**
- Modify: `skills/publish-video/scripts/publish_video.py`
- Test: `skills/publish-video/scripts/test_publish_video.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_publish_video.py` inside the `Helpers` class (after `test_build_ytdlp_cmd_without_cookies`):

```python
    def test_build_ytdlp_cmd_concurrent_fragments(self):
        cmd = v.build_ytdlp_cmd("URL", "/o.mp4", None, "vcodec:h264", concurrent_fragments=4)
        self.assertIn("-N", cmd)
        self.assertIn("4", cmd)

    def test_build_ytdlp_cmd_no_fragments_when_one(self):
        cmd = v.build_ytdlp_cmd("URL", "/o.mp4", None, "vcodec:h264", concurrent_fragments=1)
        self.assertNotIn("-N", cmd)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/kunwu/Workspace/playground/publish-video-plugin/skills/publish-video/scripts && python3 -m unittest test_publish_video.Helpers.test_build_ytdlp_cmd_concurrent_fragments -v`
Expected: FAIL — `build_ytdlp_cmd()` got an unexpected keyword argument `concurrent_fragments`.

- [ ] **Step 3: Implement — thread the flag through the call chain**

In `publish_video.py`, replace the `build_ytdlp_cmd` function with:

```python
def build_ytdlp_cmd(url: str, out_path: str, cookies_from_browser, format_sort: str,
                    concurrent_fragments: int = 1):
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f",
        AVC1_FORMAT,
        "-S",
        format_sort,
        "--merge-output-format",
        "mp4",
        "-o",
        out_path,
    ]
    if concurrent_fragments and concurrent_fragments > 1:
        cmd += ["-N", str(concurrent_fragments)]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    cmd += ["--", url]
    return cmd
```

Replace `download_and_mux` with:

```python
def download_and_mux(url: str, out_path: str, cookies, format_sort: str,
                     concurrent_fragments: int = 1):
    cmd = build_ytdlp_cmd(url, out_path, cookies, format_sort, concurrent_fragments)
    print("+ " + " ".join(cmd), file=sys.stderr)
    # Route yt-dlp's own stdout (progress/info) to our stderr so stdout stays pure JSON.
    if subprocess.run(cmd, stdout=sys.stderr).returncode != 0:
        raise PublishError("yt-dlp download/mux failed (see output above)")
```

Replace the `acquire` function's signature and its `ytdlp_url` branch. The current function is:

```python
def acquire(source: str, stype: str, workdir: str, cookies, format_sort: str) -> str:
    if stype == "ytdlp_url":
        out = os.path.join(workdir, "video.mp4")
        download_and_mux(source, out, cookies, format_sort)
        return out
```

Change it to:

```python
def acquire(source: str, stype: str, workdir: str, cookies, format_sort: str,
            concurrent_fragments: int = 1) -> str:
    if stype == "ytdlp_url":
        out = os.path.join(workdir, "video.mp4")
        download_and_mux(source, out, cookies, format_sort, concurrent_fragments)
        return out
```

(Leave the rest of `acquire` — the `direct_url`/`local_file` branches — unchanged.)

In `process_job`, the current acquire call is:

```python
        acquired = acquire(source, stype, workdir, args.cookies, args.format_sort)
```

Change it to:

```python
        acquired = acquire(source, stype, workdir, args.cookies, args.format_sort,
                          args.concurrent_fragments)
```

In `main`, add this argument right after the `--format-sort` argument:

```python
    p.add_argument("--concurrent-fragments", dest="concurrent_fragments", type=int, default=1,
                   help="yt-dlp -N: parallel fragment downloads per video (default: 1)")
```

- [ ] **Step 4: Run to verify they pass + full engine suite**

Run: `cd /Users/kunwu/Workspace/playground/publish-video-plugin/skills/publish-video/scripts && python3 -m unittest test_publish_video -v`
Expected: PASS, including the 2 new tests.

Also smoke-test the CLI surface: `python3 publish_video.py --help` — confirm `--concurrent-fragments` is listed, exit 0.

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/publish_video.py skills/publish-video/scripts/test_publish_video.py
git commit -m "feat: engine --concurrent-fragments (yt-dlp -N) for parallel fragment downloads"
```

---

## Task 2: Config defaults + `--concurrency` CLI (`watcher.py`)

**Files:**
- Modify: `skills/publish-video/scripts/watcher.py`
- Test: `skills/publish-video/scripts/test_watcher.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_watcher.py` in the `Config` class (after `test_default_max_items_is_10`):

```python
    def test_default_concurrency_and_fragments(self):
        cfg = w.parse_config('')
        self.assertEqual(cfg["concurrency"], 5)
        self.assertEqual(cfg["concurrent_fragments"], 4)
```

Add to the `Cli` class (after `test_parse_args_limit`):

```python
    def test_parse_args_concurrency(self):
        self.assertIsNone(w.parse_args([]).concurrency)
        self.assertEqual(w.parse_args(["--concurrency", "2"]).concurrency, 2)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/kunwu/Workspace/playground/publish-video-plugin/skills/publish-video/scripts && python3 -m unittest test_watcher.Config.test_default_concurrency_and_fragments test_watcher.Cli.test_parse_args_concurrency -v`
Expected: FAIL — `KeyError: 'concurrency'` and `AttributeError: 'Namespace' object has no attribute 'concurrency'`.

- [ ] **Step 3: Implement**

In `watcher.py`, in `DEFAULT_CONFIG`, add two keys right after the `max_items` line:

```python
    "max_items": 10,  # cap each source to its N latest items per pass (0 = no cap)
    "concurrency": 5,  # how many videos to download/upload at once
    "concurrent_fragments": 4,  # yt-dlp -N: parallel fragment downloads per video
```

In `parse_args`, add this argument after the `--limit` argument:

```python
    p.add_argument("--concurrency", type=int, help="how many videos to publish at once (overrides config)")
```

In `main`, add the override right after the existing `--limit` override block (after `cfg["max_items"] = args.limit`):

```python
    if args.concurrency is not None:
        cfg["concurrency"] = args.concurrency
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd /Users/kunwu/Workspace/playground/publish-video-plugin/skills/publish-video/scripts && python3 -m unittest test_watcher.Config test_watcher.Cli -v`
Expected: PASS (including the 2 new tests).

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: watcher concurrency + concurrent_fragments config and --concurrency flag"
```

---

## Task 3: Thread `concurrent_fragments` through the publish call (`watcher.py`)

**Files:**
- Modify: `skills/publish-video/scripts/watcher.py`
- Test: `skills/publish-video/scripts/test_watcher.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_watcher.py` in the `Publish` class (after `test_build_publish_cmd_transcode`):

```python
    def test_build_publish_cmd_concurrent_fragments(self):
        cmd = w.build_publish_cmd("URL", "/p.py", transcode=False, cookies_browser="chrome",
                                  concurrent_fragments=4)
        self.assertIn("--concurrent-fragments", cmd)
        self.assertIn("4", cmd)

    def test_build_publish_cmd_no_fragments_when_one(self):
        cmd = w.build_publish_cmd("URL", "/p.py", transcode=False, cookies_browser="chrome",
                                  concurrent_fragments=1)
        self.assertNotIn("--concurrent-fragments", cmd)

    def test_run_publish_passes_fragments(self):
        calls = {}

        def fake_run(cmd, capture_output, text):
            calls["cmd"] = cmd
            return FakeProc(stdout=json.dumps(
                {"results": [{"public_url": "u", "duration_secs": 1, "title": "t"}]}))

        w.run_publish("URL", "/p.py", False, "chrome", concurrent_fragments=4, run_fn=fake_run)
        self.assertIn("--concurrent-fragments", calls["cmd"])
        self.assertIn("4", calls["cmd"])
```

Add to the `Orchestrate` class (after `test_process_entry_logs_published_url`):

```python
    def test_process_entry_passes_fragments_to_publish(self):
        entry = {"platform": "youtube", "id": "a", "url": "u", "title": "t"}
        cfg = w.parse_config('')   # concurrent_fragments defaults to 4
        got = {}

        def publish(url, script, transcode, cookies, concurrent_fragments=1):
            got["frag"] = concurrent_fragments
            return {"results": [{"public_url": "x", "duration_secs": 1, "title": "t"}]}

        w.process_entry(entry, cfg, "/p.py", _base_deps({"publish": publish}), log=lambda m: None)
        self.assertEqual(got["frag"], 4)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/kunwu/Workspace/playground/publish-video-plugin/skills/publish-video/scripts && python3 -m unittest test_watcher.Publish.test_run_publish_passes_fragments test_watcher.Orchestrate.test_process_entry_passes_fragments_to_publish -v`
Expected: FAIL — `build_publish_cmd()`/`run_publish()` got an unexpected keyword argument `concurrent_fragments`, and `got['frag']` not set.

- [ ] **Step 3: Implement**

In `watcher.py`, replace `build_publish_cmd` with:

```python
def build_publish_cmd(url, script_path, transcode, cookies_browser, concurrent_fragments=1) -> list:
    cmd = ["python3", script_path, url, "--cookies-from-browser", cookies_browser]
    if transcode:
        cmd.append("--transcode")
    if concurrent_fragments and concurrent_fragments > 1:
        cmd += ["--concurrent-fragments", str(concurrent_fragments)]
    return cmd
```

Replace `run_publish`'s signature and its `build_publish_cmd` call. The current first two lines are:

```python
def run_publish(url, script_path, transcode, cookies_browser, run_fn=subprocess.run) -> dict:
    cmd = build_publish_cmd(url, script_path, transcode, cookies_browser)
```

Change them to (keep the rest of the function body unchanged):

```python
def run_publish(url, script_path, transcode, cookies_browser, concurrent_fragments=1,
                run_fn=subprocess.run) -> dict:
    cmd = build_publish_cmd(url, script_path, transcode, cookies_browser, concurrent_fragments)
```

In `process_entry`, replace the publish call. The current line is:

```python
    envelope = deps["publish"](entry["url"], script_path, cfg["transcode"], cfg["cookies_browser"])
```

Change it to:

```python
    envelope = deps["publish"](entry["url"], script_path, cfg["transcode"], cfg["cookies_browser"],
                               concurrent_fragments=cfg["concurrent_fragments"])
```

Now update the test fakes that stand in for `publish` so they accept the new keyword arg. In `test_watcher.py`:

In `_base_deps`, change the `publish` lambda from:

```python
        "publish": lambda url, script, transcode, cookies: {
            "results": [{"public_url": "https://b/x.mp4", "duration_secs": 5, "title": "T"}]},
```

to:

```python
        "publish": lambda url, script, transcode, cookies, concurrent_fragments=1: {
            "results": [{"public_url": "https://b/x.mp4", "duration_secs": 5, "title": "T"}]},
```

In `test_process_entry_publish_error_skips_actions`, change the publish override from `lambda *a:` to `lambda *a, **k:`:

```python
            "publish": lambda *a, **k: {"results": [{"error": "download failed"}]},
```

In `test_tick_marks_only_successful_seen`, change the inner `publish` signature from `def publish(url, script, transcode, cookies):` to:

```python
        def publish(url, script, transcode, cookies, concurrent_fragments=1):
```

In `test_tick_isolates_listing_failure`, change `def publish(*a):` to:

```python
        def publish(*a, **k):
```

- [ ] **Step 4: Run to verify they pass + full suite**

Run: `cd /Users/kunwu/Workspace/playground/publish-video-plugin/skills/publish-video/scripts && python3 -m unittest test_watcher test_publish_video -v`
Expected: PASS (all, including the new Publish + Orchestrate tests; the updated fakes keep the existing Orchestrate tests green).

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: pass concurrent_fragments through the watcher publish call"
```

---

## Task 4: Parallel `tick` (thread pool + locked state writes)

**Files:**
- Modify: `skills/publish-video/scripts/watcher.py`
- Test: `skills/publish-video/scripts/test_watcher.py`

- [ ] **Step 1: Write the failing test**

Add to `test_watcher.py` in the `Orchestrate` class (after `test_tick_isolates_listing_failure`):

```python
    def test_tick_processes_all_fresh_via_pool(self):
        entries = [{"platform": "youtube", "id": f"v{i}", "url": f"u{i}", "title": "t"}
                   for i in range(3)]
        import threading as _t
        saved = {"keys": set()}
        save_lock = _t.Lock()

        def save(path, keys):
            with save_lock:
                saved["keys"] = set(keys)

        cfg = w.parse_config('')            # concurrency defaults to 5
        cfg["platforms"] = {"youtube": {"source": "watch_later"}}
        deps = _base_deps({"list_entries": lambda *a, **k: entries, "save_state": save})
        handled = w.tick(cfg, "/p.py", deps, log=lambda m: None)
        self.assertEqual(len(handled), 3)
        self.assertTrue(all(o["ok"] for o in handled))
        self.assertEqual(saved["keys"], {"youtube:v0", "youtube:v1", "youtube:v2"})

    def test_tick_contains_worker_exception(self):
        entries = [{"platform": "youtube", "id": "boom", "url": "u", "title": "t"}]

        def publish(*a, **k):
            raise RuntimeError("publish exploded")

        cfg = w.parse_config('')
        cfg["platforms"] = {"youtube": {"source": "watch_later"}}
        deps = _base_deps({"list_entries": lambda *a, **k: entries, "publish": publish})
        handled = w.tick(cfg, "/p.py", deps, log=lambda m: None)
        self.assertEqual(len(handled), 1)
        self.assertFalse(handled[0]["ok"])   # contained, not raised
```

- [ ] **Step 2: Run to verify the new exception test fails**

Run: `cd /Users/kunwu/Workspace/playground/publish-video-plugin/skills/publish-video/scripts && python3 -m unittest test_watcher.Orchestrate.test_tick_contains_worker_exception -v`
Expected: FAIL — the current serial `tick` lets the `RuntimeError` propagate out (the test sees an error/raise, not a contained `ok: False`).

(`test_tick_processes_all_fresh_via_pool` may already pass under the serial loop — that's fine; it locks in behavior that must hold after the rewrite too.)

- [ ] **Step 3: Implement the parallel tick**

In `watcher.py`, add these imports next to the existing `import` lines (after `import time`):

```python
import threading
from concurrent.futures import ThreadPoolExecutor
```

Replace the entire `tick` function with:

```python
def tick(cfg, script_path, deps, log) -> list:
    seen = deps["load_state"](cfg["state_path"])
    # Listing phase (serial, cheap): snapshot all fresh entries against `seen` before
    # the pool starts, so the dedup decision is race-free.
    fresh_all = []
    for platform, pconf in cfg["platforms"].items():
        try:
            entries = deps["list_entries"](platform, pconf["source"], cfg["cookies_browser"],
                                           max_items=cfg["max_items"])
        except Exception as e:  # one platform's listing failing must not stop the others
            log(f"listing {platform} failed: {e}")
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
        return list(pool.map(work, fresh_all))
```

- [ ] **Step 4: Run to verify they pass + full suite**

Run: `cd /Users/kunwu/Workspace/playground/publish-video-plugin/skills/publish-video/scripts && python3 -m unittest test_watcher test_publish_video -v`
Expected: PASS (all). The pre-existing `test_tick_marks_only_successful_seen` and `test_tick_isolates_listing_failure` still pass because correctness is preserved through the pool.

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: parallel tick via ThreadPoolExecutor with locked state writes"
```

---

## Task 5: Document the knobs (`watcher.example.toml`, `REFERENCE.md`)

**Files:**
- Modify: `skills/publish-video/scripts/watcher.example.toml`
- Modify: `skills/publish-video/REFERENCE.md`

- [ ] **Step 1: Update the config template**

In `watcher.example.toml`, the current top block is:

```toml
poll_interval_mins = 60        # loop mode only; ignored with --once
transcode = false              # re-encode non-H.264/AAC inputs before upload
max_items = 10                 # only the N latest items per source each pass (0 = no cap)
cookies_browser = "chrome"     # browser yt-dlp reads cookies from (Watch Later is private)
```

Replace it with:

```toml
poll_interval_mins = 60        # loop mode only; ignored with --once
transcode = false              # re-encode non-H.264/AAC inputs before upload
max_items = 10                 # only the N latest items per source each pass (0 = no cap)
concurrency = 5                # how many videos to download/upload at once
concurrent_fragments = 4       # yt-dlp -N: parallel fragment downloads per video
cookies_browser = "chrome"     # browser yt-dlp reads cookies from (Watch Later is private)
```

- [ ] **Step 2: Update REFERENCE.md — CLI table**

In `skills/publish-video/REFERENCE.md`, the watcher CLI table currently ends with the `--limit` row. Add this row immediately after it:

```markdown
| `--concurrency N` | config `concurrency` | How many videos to download/upload at once this run |
```

- [ ] **Step 3: Update REFERENCE.md — Config bullets**

In the watcher "### Config" list, add these two bullets immediately after the `max_items` bullet:

```markdown
- `concurrency` — how many videos download/upload at once (default 5). A bounded thread pool; each video runs in its own engine subprocess.
- `concurrent_fragments` — passed to yt-dlp as `-N` (default 4), parallelizing one video's fragment downloads. Speeds up a single large video.
```

- [ ] **Step 4: Verify the full suite still green (docs-only, sanity)**

Run: `cd /Users/kunwu/Workspace/playground/publish-video-plugin/skills/publish-video/scripts && python3 -m unittest test_watcher test_publish_video`
Expected: PASS (no code changed, but confirm nothing was disturbed).

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher.example.toml skills/publish-video/REFERENCE.md
git commit -m "docs: document concurrency + concurrent_fragments + --concurrency"
```

---

## Self-Review

Checked against the spec (`2026-06-15-watcher-parallel-publishing-design.md`):

- **Two knobs (`concurrency`=5, `concurrent_fragments`=4)** → Task 2 (config + CLI). ✔
- **Engine `-N` flag** → Task 1 (`build_ytdlp_cmd` injects `-N`, threaded through `download_and_mux`/`acquire`/`process_job`/argparse). ✔
- **`concurrent_fragments` reaches the engine** → Task 3 (`build_publish_cmd`/`run_publish`/`process_entry`). ✔
- **Parallel tick (pool + lock, snapshot-before-pool dedup)** → Task 4. ✔
- **State safety (lock around seen+save_state)** → Task 4. ✔
- **Error handling (per-item containment, per-platform listing isolation)** → Task 4 (`work` try/except; listing try/except retained). ✔
- **`--dry-run` stays serial** → not touched by any task (only `tick` parallelized). ✔
- **Docs** → Task 5. ✔
- **Placeholder scan:** none; every code step has complete code. ✔
- **Type/signature consistency:** `concurrent_fragments` is a trailing keyword with default `1` everywhere it's added (`build_ytdlp_cmd`, `download_and_mux`, `acquire`, `build_publish_cmd`, `run_publish`), so existing positional calls stay valid; `process_entry` passes it by keyword; every `publish` fake is updated to accept the keyword (Task 3). `tick` reads `cfg["concurrency"]` which Task 2 guarantees exists. ✔
- **Note:** Task 4 intentionally changes one prior behavior — a worker exception (e.g. engine exit 2 raised by `run_publish`) is now *contained* per item (logged, `ok: False`) instead of propagating out of `tick`. This matches the spec's error-handling section and is the correct behavior for a batch processor; the engine-stderr forwarding (already in place) surfaces the real reason.
