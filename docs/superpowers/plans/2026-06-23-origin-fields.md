# Origin Fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach `platform`/`origin_url` to the publish JSON result and inject `origin`/`origin_url` into video-summarizer markdown frontmatter.

**Architecture:** Three focused changes in three existing files. `publish_video.py` gains a `platform` field via the existing `detect_platform()`. `watcher.py` forwards `entry["url"]` as `origin_url` in `make_result`. `watcher_actions.py` gains a module-level `_inject_frontmatter()` helper that post-processes the `.md` file written by the summarizer CLI.

**Tech Stack:** Python 3 stdlib only (no new dependencies).

## Global Constraints

- No new modules or files — all changes in the three files listed.
- No changes to `state.json` schema, TOML config, or CLI flags.
- `_inject_frontmatter` must be safe: never raise, never fail the summarize step.
- Test runner: `cd skills/publish-video/scripts && python3 -m unittest test_publish_video test_watcher -v`

---

### Task 1: Add `platform` to `build_result` in `publish_video.py`

**Files:**
- Modify: `skills/publish-video/scripts/publish_video.py:372-377`
- Test: `skills/publish-video/scripts/test_publish_video.py:287-293`

**Interfaces:**
- Consumes: existing `detect_platform(source: str) -> str` (line 123 of same file)
- Produces: `build_result(...)` dict now includes `"platform": str`

- [ ] **Step 1: Write the failing test**

In `test_publish_video.py`, inside the existing `class Results`, update `test_build_result` to also assert `platform` is present:

```python
def test_build_result(self):
    r = v.build_result("src", "local_file", "T", "https://b/k.mp4", "k.mp4", 12, True, False)
    self.assertEqual(r["public_url"], "https://b/k.mp4")
    self.assertEqual(r["duration_secs"], 12)
    self.assertTrue(r["passthrough"])
    self.assertNotIn("error", r)
    self.assertEqual(r["platform"], "local")

def test_build_result_bilibili_platform(self):
    r = v.build_result(
        "https://www.bilibili.com/video/BV1abc", "ytdlp_url",
        "T", "https://r2/v.mp4", "v.mp4", 60, False, False,
    )
    self.assertEqual(r["platform"], "bilibili")

def test_build_result_youtube_platform(self):
    r = v.build_result(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "ytdlp_url",
        "T", "https://r2/v.mp4", "v.mp4", 60, False, False,
    )
    self.assertEqual(r["platform"], "youtube")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd skills/publish-video/scripts && python3 -m unittest test_publish_video.Results.test_build_result test_publish_video.Results.test_build_result_bilibili_platform test_publish_video.Results.test_build_result_youtube_platform -v
```

Expected: FAIL — `KeyError: 'platform'` or `AssertionError`

- [ ] **Step 3: Implement — add `platform` to `build_result`**

In `publish_video.py`, change `build_result` (line 372):

```python
def build_result(source, stype, title, public, key, duration, passthrough, transcoded) -> dict:
    return {
        "source": source, "type": stype, "platform": detect_platform(source), "title": title,
        "public_url": public, "object_key": key, "duration_secs": duration,
        "passthrough": passthrough, "transcoded": transcoded,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd skills/publish-video/scripts && python3 -m unittest test_publish_video.Results.test_build_result test_publish_video.Results.test_build_result_bilibili_platform test_publish_video.Results.test_build_result_youtube_platform -v
```

Expected: OK (3 tests)

- [ ] **Step 5: Run full test suite**

```bash
cd skills/publish-video/scripts && python3 -m unittest test_publish_video test_watcher -v
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add skills/publish-video/scripts/publish_video.py skills/publish-video/scripts/test_publish_video.py
git commit -m "feat: add platform field to build_result via detect_platform"
```

---

### Task 2: Add `origin_url` to `make_result` in `watcher.py`

**Files:**
- Modify: `skills/publish-video/scripts/watcher.py:102-109`
- Test: `skills/publish-video/scripts/test_watcher.py:814-819`

**Interfaces:**
- Consumes: `entry["url"]` (already present on every entry from `watcher_sources.list_entries`)
- Produces: `make_result(entry, published)` dict now includes `"origin_url": str`

- [ ] **Step 1: Write the failing test**

In `test_watcher.py`, update the existing `test_make_result` (line 814):

```python
def test_make_result(self):
    entry = {"platform": "youtube", "id": "abc",
             "url": "https://www.youtube.com/watch?v=abc", "title": "fallback"}
    published = {"public_url": "https://b/x.mp4", "duration_secs": 9, "title": "Real"}
    r = w.make_result(entry, published)
    self.assertEqual(r, {
        "platform": "youtube",
        "source_id": "abc",
        "origin_url": "https://www.youtube.com/watch?v=abc",
        "title": "Real",
        "public_url": "https://b/x.mp4",
        "duration_secs": 9,
    })
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd skills/publish-video/scripts && python3 -m unittest test_watcher.Publish.test_make_result -v
```

Expected: FAIL — `AssertionError: {'platform': ...} != {'platform': ..., 'origin_url': ...}`

- [ ] **Step 3: Implement — add `origin_url` to `make_result`**

In `watcher.py`, change `make_result` (line 102):

```python
def make_result(entry: dict, published: dict) -> dict:
    return {
        "platform": entry["platform"],
        "source_id": entry["id"],
        "origin_url": entry["url"],
        "title": published.get("title", entry.get("title", "")),
        "public_url": published["public_url"],
        "duration_secs": published.get("duration_secs", 0),
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd skills/publish-video/scripts && python3 -m unittest test_watcher.Publish.test_make_result -v
```

Expected: OK

- [ ] **Step 5: Run full test suite**

```bash
cd skills/publish-video/scripts && python3 -m unittest test_publish_video test_watcher -v
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add skills/publish-video/scripts/watcher.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: forward entry url as origin_url in watcher make_result"
```

---

### Task 3: Inject `origin` + `origin_url` into markdown frontmatter

**Files:**
- Modify: `skills/publish-video/scripts/watcher_actions.py`
- Test: `skills/publish-video/scripts/test_watcher.py` (new tests in the existing summarize test class)

**Interfaces:**
- Consumes: `r["platform"]` and `r["origin_url"]` from Task 2's `make_result` output; the `.md` path returned by the summarizer CLI
- Produces: `_inject_frontmatter(path, platform, origin_url, open_fn=open, log=None)` — module-level helper; `summarize_one` calls it after a successful `.md` path is confirmed

- [ ] **Step 1: Write failing tests**

Find the existing summarize test class in `test_watcher.py` (search for `test_summarize_action_no_items_skips` to locate the class) and add these new tests after the existing ones:

```python
def test_inject_frontmatter_adds_origin_fields(self):
    content = "---\ntitle: T\nsource: https://r2/v.mp4\nduration: '1:00'\n---\n# T\n"
    written = []
    def fake_open(path, mode="r", encoding="utf-8"):
        import io
        if mode == "r":
            return io.StringIO(content)
        buf = io.StringIO()
        buf.close = lambda: written.append(buf.getvalue())
        return buf
    act._inject_frontmatter("/out/v.md", "bilibili",
                            "https://www.bilibili.com/video/BV1xxx",
                            open_fn=fake_open)
    self.assertEqual(len(written), 1)
    fm = written[0]
    self.assertIn("origin: bilibili\n", fm)
    self.assertIn("origin_url: https://www.bilibili.com/video/BV1xxx\n", fm)
    # origin fields appear before closing ---
    close = fm.index("\n---\n", 3)
    inject_pos = fm.index("origin: bilibili")
    self.assertLess(inject_pos, close)

def test_inject_frontmatter_no_frontmatter_is_noop(self):
    content = "# No frontmatter here\n\nsome body"
    written = []
    def fake_open(path, mode="r", encoding="utf-8"):
        import io
        if mode == "r":
            return io.StringIO(content)
        buf = io.StringIO()
        buf.close = lambda: written.append(buf.getvalue())
        return buf
    act._inject_frontmatter("/out/v.md", "bilibili", "https://b.com/v",
                            open_fn=fake_open)
    self.assertEqual(written, [])  # nothing written

def test_inject_frontmatter_already_present_is_noop(self):
    content = "---\ntitle: T\norigin: bilibili\norigin_url: https://b.com/v\n---\n# T\n"
    written = []
    def fake_open(path, mode="r", encoding="utf-8"):
        import io
        if mode == "r":
            return io.StringIO(content)
        buf = io.StringIO()
        buf.close = lambda: written.append(buf.getvalue())
        return buf
    act._inject_frontmatter("/out/v.md", "bilibili", "https://b.com/v",
                            open_fn=fake_open)
    self.assertEqual(written, [])  # nothing written

def test_summarize_action_injects_origin_into_md(self):
    # When origin_url is on the result, summarize_one injects it after writing the md.
    injected = []
    content = "---\ntitle: T\nsource: https://r2/v.mp4\n---\n# T\n"
    def fake_run(cmd, **kw):
        return _Proc(stdout="/out/v.md\n", returncode=0)
    def fake_inject(path, platform, origin_url, open_fn=None, log=None):
        injected.append((path, platform, origin_url))
    result_with_origin = {
        "platform": "bilibili",
        "origin_url": "https://www.bilibili.com/video/BV1xxx",
        "title": "T",
        "public_url": "https://r2/v.mp4",
        "duration_secs": 60,
    }
    act.summarize_action(
        {"outcomes": [{"ok": True, "result": result_with_origin}],
         "listing_errors": [], "summary": "s"},
        {"enabled": True, "out": "/out", "notify": False},
        run_fn=fake_run, send_fn=lambda *a: None,
        inject_fn=fake_inject,
    )
    self.assertEqual(injected, [("/out/v.md", "bilibili",
                                 "https://www.bilibili.com/video/BV1xxx")])

def test_summarize_action_skips_inject_without_origin_url(self):
    # Results without origin_url (e.g. direct-publish items) must not crash.
    injected = []
    def fake_run(cmd, **kw):
        return _Proc(stdout="/out/v.md\n", returncode=0)
    def fake_inject(path, platform, origin_url, open_fn=None, log=None):
        injected.append((path, platform, origin_url))
    result_no_origin = {
        "platform": "youtube", "title": "T",
        "public_url": "https://r2/v.mp4", "duration_secs": 60,
    }
    act.summarize_action(
        {"outcomes": [{"ok": True, "result": result_no_origin}],
         "listing_errors": [], "summary": "s"},
        {"enabled": True, "out": "/out", "notify": False},
        run_fn=fake_run, send_fn=lambda *a: None,
        inject_fn=fake_inject,
    )
    self.assertEqual(injected, [])  # inject never called
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd skills/publish-video/scripts && python3 -m unittest test_watcher -k "inject_frontmatter or injects_origin or skips_inject" -v
```

Expected: FAIL — `AttributeError: module 'watcher_actions' has no attribute '_inject_frontmatter'`

- [ ] **Step 3: Implement `_inject_frontmatter` as a module-level function**

Add this function near the top of `watcher_actions.py`, before `summarize_action` (after the existing imports):

```python
def _inject_frontmatter(path, platform, origin_url, open_fn=open, log=None):
    """Inject origin + origin_url into a markdown file's YAML frontmatter.
    Safe: silently no-ops if frontmatter is absent, fields already present,
    origin_url is empty, or any I/O error occurs."""
    log = log or (lambda m: None)
    try:
        with open_fn(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        log(f"summarize frontmatter: could not read {path}: {e}")
        return
    if not content.startswith("---\n"):
        log(f"summarize frontmatter: no frontmatter block in {path}")
        return
    if "\norigin:" in content or "\norigin_url:" in content:
        return
    close = content.find("\n---\n", 3)
    if close == -1:
        log(f"summarize frontmatter: no closing --- in {path}")
        return
    injection = f"origin: {platform}\norigin_url: {origin_url}\n"
    new_content = content[:close + 1] + injection + content[close + 1:]
    try:
        with open_fn(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except OSError as e:
        log(f"summarize frontmatter: could not write {path}: {e}")
```

- [ ] **Step 4: Wire injection into `summarize_one` inside `summarize_action`**

`summarize_action` currently takes `(run_context, opts, log=None, env=None, run_fn=subprocess.run, send_fn=send_macos_notification)`. Add `inject_fn=_inject_frontmatter` as a new keyword argument so tests can swap it out.

Change the function signature and the `summarize_one` inner function:

```python
def summarize_action(run_context, opts, log=None, env=None,
                     run_fn=subprocess.run, send_fn=send_macos_notification,
                     inject_fn=_inject_frontmatter) -> dict:
```

Inside `summarize_one`, after the `if path.endswith(".md"):` block, add the injection call before the `return`:

```python
        if path.endswith(".md"):
            origin_url = r.get("origin_url", "")
            platform = r.get("platform", "")
            if origin_url and platform:
                inject_fn(path, platform, origin_url, log=log)
            return {"title": r["title"], "path": path}
```

- [ ] **Step 5: Run new tests to verify they pass**

```bash
cd skills/publish-video/scripts && python3 -m unittest test_watcher -k "inject_frontmatter or injects_origin or skips_inject" -v
```

Expected: OK (5 tests)

- [ ] **Step 6: Run full test suite**

```bash
cd skills/publish-video/scripts && python3 -m unittest test_publish_video test_watcher -v
```

Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add skills/publish-video/scripts/watcher_actions.py skills/publish-video/scripts/test_watcher.py
git commit -m "feat: inject origin + origin_url into summarizer markdown frontmatter"
```
