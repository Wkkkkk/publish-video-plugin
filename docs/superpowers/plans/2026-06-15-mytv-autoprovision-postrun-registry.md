# Self-provisioning MyTV + Post-Run Action Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the watcher auto-create per-platform MyTV channels and register items into them, and generalize run-level steps (notify, mytv) into a pluggable `[[post_run]]` action registry.

**Architecture:** Engine gains MyTV channel helpers (`list_channels`/`create_channel`/`ensure_channel`). `watcher_actions.py` gains a run-level registry (`POST_RUN_ACTIONS` + `run_post_run`), with `notify` (folded from `notify_run`) and a new `mytv_action` (group successful items by platform → ensure channel once → register each). `watcher.py` exposes `run_post_run` via `build_deps` and calls it from `run_once`; config moves to a `[[post_run]]` array.

**Tech Stack:** Python 3 stdlib (`unittest`, `urllib`, dependency injection), `tomllib`.

**Spec:** `docs/superpowers/specs/2026-06-15-mytv-autoprovision-postrun-registry-design.md`

**Test commands** (from `skills/publish-video/scripts/`):
- Engine: `python3 -m unittest test_publish_video -v`
- Watcher: `python3 -m unittest test_watcher -v`
- Both: `python3 -m unittest test_publish_video test_watcher`

**Module import aliases in tests:** `import watcher as w`, `import watcher_actions as act`, `import publish_video as v`. Watcher test fixture: `_base_deps(overrides)` (overrides is a required dict).

Commit trailer (exact) for every commit: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: Engine MyTV channel helpers

**Files:**
- Modify: `skills/publish-video/scripts/publish_video.py` (add helpers near `register_item`, ~line 407)
- Test: `skills/publish-video/scripts/test_publish_video.py`

The API (confirmed against the MyTV server): `GET /api/admin/channels` → `[{id,name,type,...}]`; `POST /api/admin/channels` with `{name, category, type, sort_order}` → 201 `{id,...}`. Both use HTTP basic auth `user:<password>`.

- [ ] **Step 1: Write the failing tests**

Add to the `Helpers` class in `test_publish_video.py`:

```python
    def test_mytv_request_builds_authed_request(self):
        captured = {}
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"ok": 1}'
        def fake_urlopen(req):
            captured["url"] = req.full_url
            captured["method"] = req.method
            captured["auth"] = req.headers.get("Authorization")
            captured["body"] = req.data
            return FakeResp()
        out = v.mytv_request("POST", "https://tv/api/admin/channels", "pw",
                             body={"name": "X"}, urlopen=fake_urlopen)
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(captured["method"], "POST")
        self.assertTrue(captured["auth"].startswith("Basic "))
        self.assertIn(b'"name": "X"', captured["body"])

    def test_list_channels_calls_get(self):
        calls = []
        api = lambda method, url, password, body=None: calls.append((method, url, body)) or [{"id": 1, "name": "A"}]
        out = v.list_channels("https://tv/", "pw", api=api)
        self.assertEqual(out, [{"id": 1, "name": "A"}])
        self.assertEqual(calls[0], ("GET", "https://tv/api/admin/channels", None))

    def test_create_channel_posts_payload(self):
        calls = []
        api = lambda method, url, password, body=None: calls.append((method, url, body)) or {"id": 9, "name": "New"}
        out = v.create_channel("https://tv/", "pw", "New", "saved", "vod_on_demand", api=api)
        self.assertEqual(out["id"], 9)
        method, url, body = calls[0]
        self.assertEqual((method, url), ("POST", "https://tv/api/admin/channels"))
        self.assertEqual(body, {"name": "New", "category": "saved",
                                "type": "vod_on_demand", "sort_order": 0})

    def test_ensure_channel_returns_existing_id_without_creating(self):
        created = []
        cid = v.ensure_channel("https://tv", "pw", "MyYoutube", "saved", "vod_on_demand",
                               existing=[{"id": 7, "name": "MyYoutube"}],
                               create_fn=lambda *a, **k: created.append(a) or {"id": 99})
        self.assertEqual(cid, 7)
        self.assertEqual(created, [])  # did NOT create

    def test_ensure_channel_creates_when_missing(self):
        created = []
        def fake_create(base, password, name, category, channel_type):
            created.append((name, category, channel_type)); return {"id": 42}
        cid = v.ensure_channel("https://tv", "pw", "MyBilibili", "saved", "vod_on_demand",
                               existing=[{"id": 7, "name": "MyYoutube"}], create_fn=fake_create)
        self.assertEqual(cid, 42)
        self.assertEqual(created, [("MyBilibili", "saved", "vod_on_demand")])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_publish_video -v 2>&1 | tail -20`
Expected: FAIL — `module 'publish_video' has no attribute 'mytv_request'` / `list_channels` / `create_channel` / `ensure_channel`.

- [ ] **Step 3: Implement the helpers**

In `publish_video.py`, immediately AFTER `register_item` (which ends ~line 425), add:

```python
def mytv_request(method, url, password, body=None, urlopen=urllib.request.urlopen) -> dict:
    token = base64.b64encode(f"user:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {token}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise PublishError(f"MyTV API {method} {url} returned {e.code}: {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        raise PublishError(f"could not reach {url}: {e.reason}")


def list_channels(base: str, password: str, api=mytv_request) -> list:
    return api("GET", f"{base.rstrip('/')}/api/admin/channels", password)


def create_channel(base: str, password: str, name: str, category: str,
                   channel_type: str, api=mytv_request) -> dict:
    body = {"name": name, "category": category, "type": channel_type, "sort_order": 0}
    return api("POST", f"{base.rstrip('/')}/api/admin/channels", password, body=body)


def ensure_channel(base: str, password: str, name: str, category: str, channel_type: str,
                   existing: list, create_fn=create_channel) -> int:
    """Return the id of the channel named `name` from `existing`, creating it if absent."""
    for ch in existing:
        if ch.get("name") == name:
            return ch["id"]
    return create_fn(base, password, name, category, channel_type)["id"]
```

Note: `mytv_request`'s default `api` binding — `list_channels`/`create_channel` reference `mytv_request` as a default arg, so it must be defined first (it is, above them).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_publish_video -v 2>&1 | tail -5`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/publish_video.py skills/publish-video/scripts/test_publish_video.py
git commit -m "$(printf 'feat: MyTV channel helpers (list/create/ensure_channel)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: Run-level action registry (`run_post_run`)

**Files:**
- Modify: `skills/publish-video/scripts/watcher_actions.py`
- Test: `skills/publish-video/scripts/test_watcher.py` (`Actions` class)

This adds the generic registry runner. The two concrete actions (notify, mytv) come in Tasks 3–4; this task tests `run_post_run` with a fake registry.

- [ ] **Step 1: Write the failing tests**

Add to the `Actions` class in `test_watcher.py`:

```python
    def test_run_post_run_dispatches_enabled(self):
        seen = []
        registry = {"a": lambda ctx, opts, log=None: seen.append(("a", opts)) or {"did": "a"}}
        cfg = [{"name": "a", "enabled": True, "x": 1}, {"name": "a", "enabled": False}]
        out = act.run_post_run({"outcomes": [], "listing_errors": [], "summary": "s"},
                               cfg, registry=registry, log=lambda m: None)
        self.assertEqual(seen, [("a", {"x": 1})])
        self.assertEqual(out[0], {"action": "a", "ok": True, "output": {"did": "a"}})

    def test_run_post_run_isolates_failure(self):
        def boom(ctx, opts, log=None): raise RuntimeError("kaboom")
        registry = {"boom": boom, "ok": lambda ctx, opts, log=None: {"fine": True}}
        cfg = [{"name": "boom", "enabled": True}, {"name": "ok", "enabled": True}]
        out = act.run_post_run({"outcomes": [], "listing_errors": [], "summary": "s"},
                               cfg, registry=registry, log=lambda m: None)
        self.assertFalse(out[0]["ok"]); self.assertEqual(out[0]["error"], "kaboom")
        self.assertTrue(out[1]["ok"])  # second action still ran

    def test_run_post_run_unknown_action(self):
        out = act.run_post_run({"outcomes": [], "listing_errors": [], "summary": "s"},
                               [{"name": "nope", "enabled": True}], registry={}, log=lambda m: None)
        self.assertFalse(out[0]["ok"]); self.assertEqual(out[0]["error"], "unknown action")
```

- [ ] **Step 2: Run to verify FAIL**

Run: `python3 -m unittest test_watcher.Actions -v 2>&1 | tail -10`
Expected: FAIL — `module 'watcher_actions' has no attribute 'run_post_run'`.

- [ ] **Step 3: Implement `run_post_run`**

In `watcher_actions.py`, add (after `run_actions`, before or after the `ACTIONS` block is fine — but `POST_RUN_ACTIONS` is defined in Tasks 3–4, so give `run_post_run` a default `registry` param that Tasks 3–4 will point at `POST_RUN_ACTIONS`). For this task, default the registry to an empty dict placeholder that Task 4 replaces:

```python
def run_post_run(run_context, post_run_config, registry=None, log=None) -> list:
    """Run-level actions. Each is fn(run_context, opts, log) -> dict; failures isolated."""
    registry = POST_RUN_ACTIONS if registry is None else registry
    log = log or (lambda m: print(m, file=sys.stderr))
    outcomes = []
    for name, opts in enabled_actions(post_run_config):
        fn = registry.get(name)
        if fn is None:
            outcomes.append({"action": name, "ok": False, "error": "unknown action"})
            log(f"post-run {name}: unknown, skipped")
            continue
        try:
            output = fn(run_context, opts, log=log)
            outcomes.append({"action": name, "ok": True, "output": output})
        except Exception as e:
            outcomes.append({"action": name, "ok": False, "error": str(e)})
            log(f"post-run {name} failed: {e}")
    return outcomes
```

Since `POST_RUN_ACTIONS` is referenced as a default fallback but not yet defined, add a temporary module-level `POST_RUN_ACTIONS = {}` near the `ACTIONS` definition for now (Tasks 3–4 populate it). The tests pass an explicit `registry`, so they don't depend on it.

- [ ] **Step 4: Run to verify PASS**

Run: `python3 -m unittest test_watcher -v 2>&1 | tail -5`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher_actions.py skills/publish-video/scripts/test_watcher.py
git commit -m "$(printf 'feat: run_post_run() run-level action registry runner\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 3: Fold `notify` into the registry (`notify_action`)

**Files:**
- Modify: `skills/publish-video/scripts/watcher_actions.py` (replace `notify_run` with `notify_action`; add to `POST_RUN_ACTIONS`)
- Test: `skills/publish-video/scripts/test_watcher.py` (`Actions` class — migrate the `test_notify_run_*` tests)

`notify_action(run_context, opts, ...)` reads counts from `run_context` and sends `run_context["summary"]` minus the `run done: ` prefix.

- [ ] **Step 1: Migrate the notify tests**

In `test_watcher.py`, the existing tests `test_notify_run_disabled`, `test_notify_run_activity_fires_on_publish`, `test_notify_run_activity_silent_on_idle`, `test_notify_run_failure_trigger_only_on_failure`, `test_notify_run_always_fires_on_idle` call `act.notify_run(result, notify_cfg, message, send_fn=...)`. Replace ALL FIVE with these `notify_action` versions (which pass `run_context` including `summary`):

```python
    def test_notify_action_disabled(self):
        sent = []
        out = act.notify_action(
            {"outcomes": [{"ok": True}], "listing_errors": [], "summary": "run done: 1 published, 0 failed"},
            {"enabled": False, "trigger": "activity"}, send_fn=lambda *a: sent.append(a))
        self.assertFalse(out["notified"]); self.assertEqual(sent, [])

    def test_notify_action_activity_fires_on_publish(self):
        sent = []
        out = act.notify_action(
            {"outcomes": [{"ok": True}], "listing_errors": [], "summary": "run done: 1 published, 0 failed"},
            {"enabled": True, "trigger": "activity", "title": "T"}, send_fn=lambda *a: sent.append(a))
        self.assertTrue(out["notified"])
        self.assertEqual(sent, [("T", "1 published, 0 failed")])  # prefix stripped

    def test_notify_action_activity_silent_on_idle(self):
        sent = []
        out = act.notify_action(
            {"outcomes": [], "listing_errors": [], "summary": "run done: 0 published, 0 failed"},
            {"enabled": True, "trigger": "activity"}, send_fn=lambda *a: sent.append(a))
        self.assertFalse(out["notified"]); self.assertEqual(sent, [])

    def test_notify_action_failure_trigger_only_on_failure(self):
        sent = []
        cfg = {"enabled": True, "trigger": "failure"}
        act.notify_action({"outcomes": [{"ok": True}], "listing_errors": [], "summary": "s"},
                          cfg, send_fn=lambda *a: sent.append(a))
        self.assertEqual(sent, [])
        act.notify_action({"outcomes": [{"ok": False}], "listing_errors": [], "summary": "s"},
                          cfg, send_fn=lambda *a: sent.append(a))
        self.assertEqual(len(sent), 1)
        act.notify_action({"outcomes": [], "listing_errors": ["youtube"], "summary": "s"},
                          cfg, send_fn=lambda *a: sent.append(a))
        self.assertEqual(len(sent), 2)

    def test_notify_action_always_fires_on_idle(self):
        sent = []
        act.notify_action({"outcomes": [], "listing_errors": [], "summary": "s"},
                          {"enabled": True, "trigger": "always"}, send_fn=lambda *a: sent.append(a))
        self.assertEqual(len(sent), 1)
```

(Leave the `test_send_macos_notification_*` tests unchanged — `send_macos_notification` is not changing.)

- [ ] **Step 2: Run to verify FAIL**

Run: `python3 -m unittest test_watcher.Actions -v 2>&1 | tail -10`
Expected: FAIL — `module 'watcher_actions' has no attribute 'notify_action'`.

- [ ] **Step 3: Replace `notify_run` with `notify_action`**

In `watcher_actions.py`, replace the entire `notify_run` function with:

```python
def notify_action(run_context, opts, log=None, send_fn=send_macos_notification) -> dict:
    """Run-level notifier driven by the run summary. Returns {notified: bool, ...}."""
    if not opts.get("enabled"):
        return {"notified": False, "reason": "disabled"}
    outcomes = run_context.get("outcomes", [])
    published = sum(1 for o in outcomes if o.get("ok"))
    failed = len(outcomes) - published
    errors = len(run_context.get("listing_errors") or [])
    trigger = opts.get("trigger", "activity")
    should = (
        trigger == "always"
        or (trigger == "failure" and (failed or errors))
        or (trigger == "activity" and (published or failed or errors))
    )
    if not should:
        return {"notified": False, "reason": "trigger not met"}
    message = run_context.get("summary", "").removeprefix("run done: ")
    send_fn(opts.get("title", "publish-video watcher"), message)
    return {"notified": True}
```

- [ ] **Step 4: Run to verify PASS**

Run: `python3 -m unittest test_watcher -v 2>&1 | tail -5`
Expected: PASS for the migrated notify tests. (`run_post_run`/`build_deps` integration still uses old wiring until Task 5; that's fine — `POST_RUN_ACTIONS` is populated in Task 4.)

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher_actions.py skills/publish-video/scripts/test_watcher.py
git commit -m "$(printf 'feat: notify_action (run-level, reads run_context)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 4: `mytv_action` + `default_channel_name` + populate registry

**Files:**
- Modify: `skills/publish-video/scripts/watcher_actions.py` (remove `run_mytv`; add `default_channel_name`, `mytv_action`; set `POST_RUN_ACTIONS`; drop `mytv` from per-video `ACTIONS`)
- Test: `skills/publish-video/scripts/test_watcher.py` (`Actions` class — remove `run_mytv` tests, add `mytv_action` tests)

- [ ] **Step 1: Migrate tests — remove `run_mytv` tests, add `mytv_action` tests**

In `test_watcher.py`, DELETE `test_run_mytv_uses_engine_helpers` and `test_run_mytv_errors_without_env` (the per-video `run_mytv` is removed). Keep `test_stubs_return_skipped`, `test_run_actions_isolates_failures`, `test_run_actions_unknown_action`. Add:

```python
    def test_default_channel_name_derives(self):
        self.assertEqual(act.default_channel_name("youtube"), "MyYoutube")
        self.assertEqual(act.default_channel_name("bilibili"), "MyBilibili")
        self.assertEqual(act.default_channel_name("vimeo"), "MyVimeo")

    def _mytv_ctx(self):
        return {"outcomes": [
            {"ok": True, "result": {"platform": "youtube", "title": "Y1",
                                    "public_url": "https://b/y1.mp4", "duration_secs": 10}},
            {"ok": True, "result": {"platform": "bilibili", "title": "B1",
                                    "public_url": "https://b/b1.mp4", "duration_secs": 20}},
            {"ok": False, "error": "x"},
        ], "listing_errors": [], "summary": "s"}

    def test_mytv_action_ensures_per_platform_and_registers(self):
        ensured, registered = [], []
        out = act.mytv_action(
            self._mytv_ctx(),
            {"enabled": True, "type": "vod_on_demand", "category": "saved",
             "channels": {"youtube": "MyYoutube", "bilibili": "MyBilibili"}},
            env={"MYTV_BASE_URL": "https://tv", "MYTV_ADMIN_PASSWORD": "pw"},
            list_channels=lambda base, pw: [],
            ensure_channel=lambda base, pw, name, cat, ctype, existing: (
                ensured.append(name) or {"MyYoutube": 1, "MyBilibili": 2}[name]),
            register_item=lambda base, cid, pw, payload: registered.append((cid, payload["title"])) or {"id": cid},
            build_payload=lambda title, url, dur: {"title": title, "url": url, "duration_secs": dur},
        )
        self.assertEqual(sorted(ensured), ["MyBilibili", "MyYoutube"])  # one ensure per platform
        self.assertEqual(sorted(registered), [(1, "Y1"), (2, "B1")])    # each item registered to its channel
        self.assertEqual(out["registered"], 2)

    def test_mytv_action_uses_derived_name_when_unconfigured(self):
        ensured = []
        act.mytv_action(
            {"outcomes": [{"ok": True, "result": {"platform": "youtube", "title": "Y",
                            "public_url": "u", "duration_secs": 1}}], "listing_errors": [], "summary": "s"},
            {"enabled": True},  # no channels map
            env={"MYTV_BASE_URL": "https://tv", "MYTV_ADMIN_PASSWORD": "pw"},
            list_channels=lambda base, pw: [],
            ensure_channel=lambda base, pw, name, cat, ctype, existing: ensured.append(name) or 5,
            register_item=lambda *a, **k: {"id": 5},
            build_payload=lambda *a, **k: {},
        )
        self.assertEqual(ensured, ["MyYoutube"])  # derived default

    def test_mytv_action_no_items_skips(self):
        out = act.mytv_action({"outcomes": [{"ok": False}], "listing_errors": [], "summary": "s"},
                              {"enabled": True}, env={"MYTV_BASE_URL": "https://tv", "MYTV_ADMIN_PASSWORD": "pw"},
                              list_channels=lambda *a: (_ for _ in ()).throw(AssertionError("should not fetch")))
        self.assertIn("skipped", out)

    def test_mytv_action_missing_env_raises(self):
        with self.assertRaises(RuntimeError):
            act.mytv_action(self._mytv_ctx(), {"enabled": True}, env={})

    def test_mytv_action_isolates_per_item_register_failure(self):
        registered = []
        def reg(base, cid, pw, payload):
            if payload["title"] == "Y1":
                raise RuntimeError("register boom")
            registered.append(payload["title"]); return {"id": cid}
        out = act.mytv_action(
            self._mytv_ctx(), {"enabled": True},
            env={"MYTV_BASE_URL": "https://tv", "MYTV_ADMIN_PASSWORD": "pw"},
            list_channels=lambda base, pw: [],
            ensure_channel=lambda *a, **k: 1,
            register_item=reg,
            build_payload=lambda title, url, dur: {"title": title, "url": url, "duration_secs": dur},
        )
        self.assertEqual(registered, ["B1"])     # B1 still registered despite Y1 failing
        self.assertEqual(out["registered"], 1)
```

- [ ] **Step 2: Run to verify FAIL**

Run: `python3 -m unittest test_watcher.Actions -v 2>&1 | tail -15`
Expected: FAIL — `module 'watcher_actions' has no attribute 'default_channel_name'`/`mytv_action`.

- [ ] **Step 3: Implement in `watcher_actions.py`**

Remove the `run_mytv` function entirely. Add (and import nothing new beyond existing `os`, `publish_video`):

```python
def default_channel_name(platform: str) -> str:
    return "My" + platform.title()


def mytv_action(run_context, opts, log=None, env=None,
                list_channels=publish_video.list_channels,
                ensure_channel=publish_video.ensure_channel,
                register_item=publish_video.register_item,
                build_payload=publish_video.build_payload) -> dict:
    """Run-level: register each successful item into its platform's MyTV channel,
    creating the channel if missing. Groups by platform; one ensure per platform."""
    log = log or (lambda m: None)
    env = os.environ if env is None else env
    base = env.get("MYTV_BASE_URL")
    password = env.get("MYTV_ADMIN_PASSWORD")
    if not base or not password:
        raise RuntimeError("mytv action needs MYTV_BASE_URL and MYTV_ADMIN_PASSWORD")
    items = [o["result"] for o in run_context.get("outcomes", []) if o.get("ok")]
    if not items:
        return {"skipped": "no items"}
    by_platform = {}
    for r in items:
        by_platform.setdefault(r["platform"], []).append(r)
    channels_cfg = opts.get("channels", {})
    ctype = opts.get("type", "vod_on_demand")
    category = opts.get("category", "")
    existing = list_channels(base, password)
    registered = 0
    channel_ids = {}
    for platform, plat_items in by_platform.items():
        name = channels_cfg.get(platform) or default_channel_name(platform)
        try:
            cid = ensure_channel(base, password, name, category, ctype, existing)
        except Exception as e:
            log(f"mytv: ensure channel {name!r} failed: {e}")
            continue
        channel_ids[platform] = cid
        for r in plat_items:
            try:
                register_item(base, cid, password,
                              build_payload(r["title"], r["public_url"], r["duration_secs"]))
                registered += 1
            except Exception as e:
                log(f"mytv: register {r.get('title')!r} failed: {e}")
    return {"registered": registered, "channels": channel_ids}
```

Then update the registries: drop `mytv` from the per-video `ACTIONS`, and define `POST_RUN_ACTIONS` (replace the temporary `POST_RUN_ACTIONS = {}` from Task 2):

```python
ACTIONS = {
    "summarize": run_summarize,
}

POST_RUN_ACTIONS = {
    "notify": notify_action,
    "mytv": mytv_action,
}
```

(`POST_RUN_ACTIONS` must be defined after `notify_action` and `mytv_action`. Place both registry dicts after those function definitions.)

- [ ] **Step 4: Run to verify PASS**

Run: `python3 -m unittest test_publish_video test_watcher -v 2>&1 | tail -5`
Expected: all PASS. Confirm no leftover refs: `grep -n "run_mytv" skills/publish-video/scripts/watcher_actions.py test_watcher.py` returns nothing.

- [ ] **Step 5: Commit**

```bash
git add skills/publish-video/scripts/watcher_actions.py skills/publish-video/scripts/test_watcher.py
git commit -m "$(printf 'feat: mytv_action (per-platform auto-create + register); drop per-video run_mytv\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 5: Wire into config + `run_once` + `build_deps`

**Files:**
- Modify: `skills/publish-video/scripts/watcher.py` (`DEFAULT_CONFIG`, `build_deps`, `run_once`)
- Modify: `skills/publish-video/scripts/watcher.toml`, `watcher.example.toml`
- Test: `skills/publish-video/scripts/test_watcher.py` (`Config`, `Orchestrate`, `Cli`)

- [ ] **Step 1: Migrate/add the failing tests**

(a) In `Config`, REPLACE `test_default_config_has_notify_block` with:

```python
    def test_default_config_has_post_run(self):
        cfg = w.parse_config("")
        names = [a.get("name") for a in cfg["post_run"]]
        self.assertEqual(names, ["notify", "mytv"])
        mytv = [a for a in cfg["post_run"] if a["name"] == "mytv"][0]
        self.assertEqual(mytv["type"], "vod_on_demand")
        self.assertEqual(mytv["channels"], {"youtube": "MyYoutube", "bilibili": "MyBilibili"})
        self.assertNotIn("notify", cfg)  # no longer a top-level block
```

(b) In `Orchestrate`, REPLACE the two `run_once` tests (`test_run_once_logs_summary_and_notifies`, `test_run_once_notify_failure_does_not_raise`) with versions that inject `run_post_run` and assert the `run_context` shape:

```python
    def test_run_once_logs_summary_and_runs_post_run(self):
        cfg = w.parse_config('')
        cfg["platforms"] = {"youtube": {"source": "watch_later"}}
        cfg["post_run"] = [{"name": "notify", "enabled": True, "trigger": "activity"}]
        seen = []
        deps = _base_deps({
            "list_entries": lambda *a, **k: [
                {"platform": "youtube", "id": "v1", "url": "u1", "title": "t"}],
            "run_post_run": lambda run_context, post_run_cfg, log=None: seen.append((run_context, post_run_cfg)),
        })
        msgs = []
        w.run_once(cfg, "/p.py", deps, log=msgs.append)
        self.assertIn("run done: 1 published, 0 failed", msgs)
        self.assertEqual(len(seen), 1)
        ctx, prcfg = seen[0]
        self.assertEqual(ctx["summary"], "run done: 1 published, 0 failed")
        self.assertEqual(len(ctx["outcomes"]), 1)
        self.assertEqual(ctx["listing_errors"], [])
        self.assertEqual(prcfg, cfg["post_run"])

    def test_run_once_post_run_failure_does_not_raise(self):
        cfg = w.parse_config('')
        cfg["platforms"] = {"youtube": {"source": "watch_later"}}
        def boom(*a, **k): raise RuntimeError("post-run boom")
        deps = _base_deps({"list_entries": lambda *a, **k: [], "run_post_run": boom})
        msgs = []
        w.run_once(cfg, "/p.py", deps, log=msgs.append)  # must not raise
        self.assertTrue(any("post-run actions failed" in m for m in msgs))
```

(c) In `Cli.test_build_deps_has_real_callables`, change the key tuple: replace `"notify"` with `"run_post_run"`:

```python
        for key in ("list_entries", "publish", "run_actions", "load_state",
                    "save_state", "new_entries", "entry_key", "run_post_run"):
```

- [ ] **Step 2: Run to verify FAIL**

Run: `python3 -m unittest test_watcher -v 2>&1 | tail -15`
Expected: `test_default_config_has_post_run` FAILS (`KeyError: 'post_run'`); the two run_once tests FAIL (still call `deps["notify"]`); build_deps test FAILS (`KeyError: 'run_post_run'`).

- [ ] **Step 3: Update `DEFAULT_CONFIG`**

In `watcher.py`, the dict currently ends:

```python
    "actions": [{"name": "mytv", "enabled": False, "channel": 0}],
    "notify": {"enabled": False, "trigger": "activity", "title": "publish-video watcher"},
}
```

Replace those two lines with:

```python
    "actions": [{"name": "summarize", "enabled": False}],
    "post_run": [
        {"name": "notify", "enabled": False, "trigger": "activity", "title": "publish-video watcher"},
        {"name": "mytv", "enabled": False, "type": "vod_on_demand", "category": "saved",
         "channels": {"youtube": "MyYoutube", "bilibili": "MyBilibili"}},
    ],
}
```

- [ ] **Step 4: Update `build_deps` and `run_once`**

In `build_deps()`, replace the line `"notify": watcher_actions.notify_run,` with:

```python
        "run_post_run": watcher_actions.run_post_run,
```

Replace the current `run_once` body (which calls `deps["notify"](result, cfg["notify"], ...)`) with:

```python
def run_once(cfg, script_path, deps, log) -> dict:
    result = tick(cfg, script_path, deps, log)
    summary = format_summary(result)
    log(summary)
    run_context = {"outcomes": result["outcomes"],
                   "listing_errors": result["listing_errors"], "summary": summary}
    try:  # post-run actions must never abort the run
        deps["run_post_run"](run_context, cfg["post_run"], log=log)
    except Exception as e:
        log(f"post-run actions failed: {e}")
    return result
```

- [ ] **Step 5: Update the TOML files**

In BOTH `watcher.toml` and `watcher.example.toml`: remove the `[notify]` block and the `[[actions]] mytv` block; keep the `[[actions]] summarize` block (in `watcher.toml`, change the `mytv` action block to the `[[post_run]]` form below — do not leave a `channel = 38` action). Read each file first, then set the post-publish section to:

```toml
# Per-video actions run in order for each newly-published video.
[[actions]]
name = "summarize"             # stub in v1 (no-op)
enabled = false

# Run-level actions run once per poll, after publishing.
[[post_run]]
name = "notify"                # macOS Notification Center alert
enabled = true
trigger = "activity"           # activity (published>0 or failed>0 or listing error) | failure | always
title = "publish-video watcher"

[[post_run]]
name = "mytv"                  # auto-register into MyTV, one channel per platform (created if missing)
enabled = true
type = "vod_on_demand"
category = "saved"
channels = { youtube = "MyYoutube", bilibili = "MyBilibili" }
```

Note: `watcher.toml` is gitignored (local config) — edit it but it won't be committed; `watcher.example.toml` IS committed.

- [ ] **Step 6: Run full suite + TOML parse check**

```bash
python3 -m unittest test_publish_video test_watcher -v 2>&1 | tail -5
python3 -c "import tomllib; tomllib.load(open('watcher.toml','rb')); tomllib.load(open('watcher.example.toml','rb')); print('TOML OK')"
```
Expected: all tests PASS; `TOML OK`.

- [ ] **Step 7: Commit**

```bash
git add skills/publish-video/scripts/watcher.py skills/publish-video/scripts/watcher.example.toml skills/publish-video/scripts/test_watcher.py
git commit -m "$(printf 'feat: [[post_run]] registry wired into run_once; retire [notify] + mytv channel id\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 6: Documentation (REFERENCE.md)

**Files:**
- Modify: `skills/publish-video/REFERENCE.md`

- [ ] **Step 1: Update the watcher config + scheduling docs**

In `skills/publish-video/REFERENCE.md`, find the watcher `### Config` bullets and the `### Scheduling` section. Make these changes:

1. The `actions` bullet currently mentions `mytv` as a per-video action. Replace it with two bullets:

```markdown
- `actions` — ordered per-video post-publish steps (run once per published video). `summarize` is a no-op stub. Add one via a function + an `ACTIONS` entry + a config block.
- `post_run` — ordered run-level actions (run once per poll, after publishing), via a parallel `[[post_run]]` registry. Built in: `notify` (macOS Notification Center) and `mytv` (auto-register published videos into MyTV). Add one via a function + a `POST_RUN_ACTIONS` entry + a `[[post_run]]` block.
```

2. Replace the Scheduling-section notify paragraph (the one starting "Set `[notify] enabled = true`") with:

```markdown
`[[post_run]]` actions run once after each poll. `notify` (macOS Notification Center)
takes `trigger` = `activity` (published or failed > 0, or a listing error) | `failure` | `always`.
`mytv` auto-registers each published video into a MyTV channel **per platform**, creating the
channel if missing: the channel name is `channels.<platform>` (e.g. `youtube = "MyYoutube"`),
defaulting to `"My" + Platform` when unset; `type` defaults to `vod_on_demand`. Needs
`MYTV_BASE_URL` + `MYTV_ADMIN_PASSWORD` in the environment.
```

3. If the JSON-output or flags section references the old per-video `mytv` action with a `channel`, leave the engine's `--sink mytv --channel` docs as-is (the engine CLI is unchanged) — only the watcher's action moved.

- [ ] **Step 2: Verify the docs read consistently**

Run: `grep -n "post_run\|mytv\|notify" skills/publish-video/REFERENCE.md`
Confirm there is no remaining claim that `mytv` is a per-video action with a fixed `channel`, and no claim that `notify` is a stub or a `[notify]` block.

- [ ] **Step 3: Commit**

```bash
git add skills/publish-video/REFERENCE.md
git commit -m "$(printf 'docs: [[post_run]] registry, per-platform auto-create mytv, notify as post-run\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 7: Live verification (controller-run, not a subagent)

**Files:** none (verification only). The controller runs this against the live MyTV instance after Tasks 1–6 merge-ready.

- [ ] **Step 1: Confirm the live watcher.toml has the `[[post_run]]` mytv block enabled** (from Task 5 Step 5), with `channels = { youtube = "MyYoutube", bilibili = "MyBilibili" }`.

- [ ] **Step 2: Faithful live test of `mytv_action` without re-downloading.** Build a one-item `run_context` pointing at an existing bucket MP4 and invoke `mytv_action` against the live instance (env from publish-video `.env`), confirming the platform channel auto-creates and the item registers:

```bash
cd /Users/kunwu/Workspace/playground/publish-video-plugin
set -a; source .env 2>/dev/null; set +a
cd skills/publish-video/scripts
python3 -c "
import os, watcher_actions as act
ctx = {'outcomes': [{'ok': True, 'result': {'platform': 'youtube', 'title': 'Me at the zoo',
       'public_url': 'https://pub-7fae8d6805af4dc6a5b2a9988274addf.r2.dev/video/youtube-20260615-jNQXAC9IVRw-Me_at_the_zoo.mp4',
       'duration_secs': 19}}], 'listing_errors': [], 'summary': 's'}
print(act.mytv_action(ctx, {'enabled': True, 'type': 'vod_on_demand', 'category': 'saved',
                            'channels': {'youtube': 'MyYoutube'}}, log=print))
"
```
Expected: prints `{'registered': 1, 'channels': {'youtube': <new id>}}`.

- [ ] **Step 3: Confirm the channel was created and holds the item:**

```bash
~/workspace/playground/mytv/target/debug/mytvctl channel list | tr ',' '\n' | grep -i MyYoutube
# then, using the printed channel id:
curl -sSL "https://kunstv.fly.dev/channel/<id>/playlist"
```
Expected: a "MyYoutube" channel exists (type `vod_on_demand`) and its playlist contains "Me at the zoo".

- [ ] **Step 4: Report** the result (channel id, item present). Note any cleanup the user may want (the test item / the channel).

---

## Notes for the implementer

- All unit tests use stdlib `unittest` + dependency injection — never make real network calls or notifications in tests; inject fakes (`api`, `create_fn`, `list_channels`/`ensure_channel`/`register_item`/`build_payload`, `send_fn`).
- Run the FULL suite (`python3 -m unittest test_publish_video test_watcher`) before each commit. Baseline at the start is 120 tests; it grows as tasks add tests and a few `run_mytv`/`notify_run` tests are replaced.
- `watcher.toml` and `.env` are gitignored (local). Only `watcher.example.toml`, the `.py` files, and docs are committed.
- Tasks are sequential and touch overlapping files — do them in order; do not parallelize implementers.
- The engine's `--sink mytv --channel N` CLI is unchanged; do not modify `process_job`/`plan_job`/argparse for MyTV.
