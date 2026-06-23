import json
import os
import tempfile
import unittest

import watcher as w
import watcher_state
import watcher_state as st
import watcher_sources as src
import watcher_actions as act


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

    def test_pending_path_sits_beside_state(self):
        self.assertEqual(
            st.pending_path_for("/a/b/state.json"), "/a/b/mytv_pending.json")

    def test_load_pending_missing_or_no_path_is_empty(self):
        self.assertEqual(st.load_pending("/no/such/pending.json"), [])
        self.assertEqual(st.load_pending(None), [])

    def test_save_then_load_pending_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "mytv_pending.json")  # nested dir must be created
            st.save_pending(path, [{"public_url": "u1"}])
            self.assertEqual(st.load_pending(path), [{"public_url": "u1"}])

    def test_save_pending_none_path_is_noop(self):
        st.save_pending(None, [{"public_url": "u1"}])  # must not raise


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

    def test_parse_listing_normalizes_na_to_empty(self):
        # yt-dlp prints the literal "NA" for a missing field (e.g. Bilibili titles
        # in --flat-playlist mode). Treat it as a missing title so it gets resolved.
        got = src.parse_listing("bilibili", "b1\thttps://bili/b1\tNA\n")
        self.assertEqual(got[0]["title"], "")

    def test_resolve_titles_fills_only_missing(self):
        entries = [
            {"platform": "bilibili", "id": "b", "url": "https://bili/b", "title": ""},
            {"platform": "youtube", "id": "y", "url": "https://yt/y", "title": "Has Title"},
        ]
        fetched = []

        def fake_fetch(url, cookies):
            fetched.append(url)
            return "Fetched Title"

        out = src.resolve_titles(entries, "chrome", fetch_fn=fake_fetch)
        self.assertEqual(out[0]["title"], "Fetched Title")
        self.assertEqual(out[1]["title"], "Has Title")   # already had one, untouched
        self.assertEqual(fetched, ["https://bili/b"])    # only the missing one fetched

    def test_resolve_titles_tolerates_fetch_failure(self):
        entries = [{"platform": "bilibili", "id": "b", "url": "u", "title": ""}]
        out = src.resolve_titles(entries, "chrome", fetch_fn=lambda url, cookies: None)
        self.assertEqual(out[0]["title"], "")            # None -> "" (no crash)

    def test_list_entries_resolves_missing_titles(self):
        def fake_run(cmd, capture_output, text):
            return FakeProc(stdout="b1\thttps://bili/b1\tNA\n")

        got = src.list_entries("bilibili", "watch_later", "chrome",
                               run_fn=fake_run, fetch_fn=lambda url, cookies: "Real Bili Title")
        self.assertEqual(got[0]["title"], "Real Bili Title")

    def test_build_list_cmd_caps_with_playlist_end(self):
        cmd = src.build_list_cmd("URL", "chrome", max_items=10)
        self.assertIn("--playlist-end", cmd)
        self.assertIn("10", cmd)

    def test_build_list_cmd_no_cap_when_none(self):
        cmd = src.build_list_cmd("URL", "chrome", max_items=None)
        self.assertNotIn("--playlist-end", cmd)

    def test_list_entries_passes_cap_to_cmd(self):
        calls = {}

        def fake_run(cmd, capture_output, text):
            calls["cmd"] = cmd
            return FakeProc(stdout="id1\thttps://x/1\tT\n")

        src.list_entries("youtube", "watch_later", "chrome",
                         run_fn=fake_run, fetch_fn=lambda u, c: "", max_items=5)
        self.assertIn("--playlist-end", calls["cmd"])
        self.assertIn("5", calls["cmd"])


SAMPLE_RESULT = {
    "platform": "youtube", "source_id": "abc", "title": "Clip",
    "public_url": "https://b/v/x.mp4", "duration_secs": 42,
}


class _Proc:  # minimal stand-in for subprocess.CompletedProcess
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class Actions(unittest.TestCase):
    def test_enabled_actions_filters_and_strips(self):
        config = [
            {"name": "mytv", "enabled": True, "channel": 7},
            {"name": "summarize", "enabled": False},
        ]
        self.assertEqual(act.enabled_actions(config), [("mytv", {"channel": 7})])

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
        self.assertEqual(sorted(ensured), ["MyBilibili", "MyYoutube"])
        self.assertEqual(sorted(registered), [(1, "Y1"), (2, "B1")])
        self.assertEqual(out["registered"], 2)

    def test_mytv_action_uses_derived_name_when_unconfigured(self):
        ensured = []
        act.mytv_action(
            {"outcomes": [{"ok": True, "result": {"platform": "youtube", "title": "Y",
                            "public_url": "u", "duration_secs": 1}}], "listing_errors": [], "summary": "s"},
            {"enabled": True},
            env={"MYTV_BASE_URL": "https://tv", "MYTV_ADMIN_PASSWORD": "pw"},
            list_channels=lambda base, pw: [],
            ensure_channel=lambda base, pw, name, cat, ctype, existing: ensured.append(name) or 5,
            register_item=lambda *a, **k: {"id": 5},
            build_payload=lambda *a, **k: {},
        )
        self.assertEqual(ensured, ["MyYoutube"])

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
        self.assertEqual(registered, ["B1"])
        self.assertEqual(out["registered"], 1)

    def test_mytv_action_isolates_per_platform_ensure_failure(self):
        registered = []
        def ensure(base, pw, name, cat, ctype, existing):
            if name == "MyYoutube":
                raise RuntimeError("ensure boom")
            return 2
        out = act.mytv_action(
            self._mytv_ctx(),
            {"enabled": True, "channels": {"youtube": "MyYoutube", "bilibili": "MyBilibili"}},
            env={"MYTV_BASE_URL": "https://tv", "MYTV_ADMIN_PASSWORD": "pw"},
            list_channels=lambda base, pw: [],
            ensure_channel=ensure,
            register_item=lambda base, cid, pw, payload: registered.append((cid, payload["title"])) or {"id": cid},
            build_payload=lambda title, url, dur: {"title": title, "url": url, "duration_secs": dur},
        )
        # youtube's ensure failed -> its item skipped; bilibili still ensured + registered
        self.assertEqual(registered, [(2, "B1")])
        self.assertEqual(out["registered"], 1)
        self.assertEqual(out["channels"], {"bilibili": 2})

    def test_mytv_action_queues_all_when_server_unreachable(self):
        # list_channels fails (MyTV offline): every item is queued, nothing lost.
        with tempfile.TemporaryDirectory() as d:
            pending = os.path.join(d, "mytv_pending.json")
            out = act.mytv_action(
                self._mytv_ctx(), {"enabled": True}, pending_path=pending,
                env={"MYTV_BASE_URL": "https://tv", "MYTV_ADMIN_PASSWORD": "pw"},
                list_channels=lambda base, pw: (_ for _ in ()).throw(RuntimeError("could not reach tv")),
            )
            self.assertEqual(out["registered"], 0)
            self.assertEqual(out["pending"], 2)
            queued = {r["title"] for r in st.load_pending(pending)}
            self.assertEqual(queued, {"Y1", "B1"})

    def test_mytv_action_retries_queued_items_then_clears(self):
        # A previously-queued item is retried on a later run and the queue is cleared.
        with tempfile.TemporaryDirectory() as d:
            pending = os.path.join(d, "mytv_pending.json")
            st.save_pending(pending, [{"platform": "youtube", "title": "Old",
                                       "public_url": "https://b/old.mp4", "duration_secs": 5}])
            registered = []
            out = act.mytv_action(
                {"outcomes": [], "listing_errors": [], "summary": "s"},
                {"enabled": True}, pending_path=pending,
                env={"MYTV_BASE_URL": "https://tv", "MYTV_ADMIN_PASSWORD": "pw"},
                list_channels=lambda base, pw: [],
                ensure_channel=lambda *a, **k: 9,
                register_item=lambda base, cid, pw, payload: registered.append(payload["title"]) or {"id": cid},
                build_payload=lambda title, url, dur: {"title": title, "url": url, "duration_secs": dur},
            )
            self.assertEqual(registered, ["Old"])
            self.assertEqual(out["registered"], 1)
            self.assertEqual(st.load_pending(pending), [])

    def test_mytv_action_failed_item_stays_queued_succeeded_clears(self):
        # Mixed run: the item that fails to register is queued; the rest clears.
        with tempfile.TemporaryDirectory() as d:
            pending = os.path.join(d, "mytv_pending.json")
            def reg(base, cid, pw, payload):
                if payload["title"] == "Y1":
                    raise RuntimeError("register boom")
                return {"id": cid}
            out = act.mytv_action(
                self._mytv_ctx(), {"enabled": True}, pending_path=pending,
                env={"MYTV_BASE_URL": "https://tv", "MYTV_ADMIN_PASSWORD": "pw"},
                list_channels=lambda base, pw: [],
                ensure_channel=lambda *a, **k: 1,
                register_item=reg,
                build_payload=lambda title, url, dur: {"title": title, "url": url, "duration_secs": dur},
            )
            self.assertEqual(out["registered"], 1)
            self.assertEqual(out["pending"], 1)
            self.assertEqual([r["title"] for r in st.load_pending(pending)], ["Y1"])

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

    def test_send_macos_notification_escapes_backslashes_before_quotes(self):
        # Order matters: backslashes must be escaped before quotes, else a literal
        # \" in the input would be double-escaped. Input  a\"b  ->  a\\\"b.
        calls = []
        act.send_macos_notification(
            't', 'a\\"b', run_fn=lambda cmd, **kw: calls.append(cmd))
        self.assertIn(r'a\\\"b', calls[0][2])

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
        self.assertEqual(sent, [("T", "1 published, 0 failed")])

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
        self.assertTrue(out[1]["ok"])

    def test_run_post_run_unknown_action(self):
        out = act.run_post_run({"outcomes": [], "listing_errors": [], "summary": "s"},
                               [{"name": "nope", "enabled": True}], registry={}, log=lambda m: None)
        self.assertFalse(out[0]["ok"]); self.assertEqual(out[0]["error"], "unknown action")

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
            return _Proc(stdout="/out/video.md\n", returncode=0)
        out = act.summarize_action(
            self._mytv_ctx(), {"enabled": True, "out": "/out", "notify": True},
            run_fn=fake_run, send_fn=lambda *a: sent.append(a))
        self.assertEqual(len(calls), 2)
        # Order of calls is nondeterministic under the parallel pool; assert the set.
        self.assertEqual({c[1] for c in calls}, {"https://b/y1.mp4", "https://b/b1.mp4"})
        for c in calls:
            self.assertIn("--out", c); self.assertIn("/out", c)
        self.assertEqual(out["summarized"], 2)
        # analyses preserve input (outcome) order even when run concurrently.
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

    def test_summarize_action_passes_clean_title(self):
        # The CLI only sees the metadata-less R2 public_url, so the watcher must
        # feed it the clean listing title via --title for a vault-ready note.
        calls = []
        act.summarize_action(
            {"outcomes": [{"ok": True, "result": {"platform": "youtube",
             "title": "讲透 Agentic Design Patterns 1/21",
             "public_url": "https://r2/x.mp4", "duration_secs": 1}}],
             "listing_errors": [], "summary": "s"},
            {"enabled": True, "notify": False},
            run_fn=lambda cmd, **kw: calls.append(cmd) or _Proc(stdout="/o/y.md", returncode=0),
            send_fn=lambda *a: None)
        self.assertIn("--title", calls[0])
        self.assertEqual(calls[0][calls[0].index("--title") + 1],
                         "讲透 Agentic Design Patterns 1/21")

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

    def test_summarize_action_passes_whisper_model(self):
        calls = []
        act.summarize_action(
            {"outcomes": [{"ok": True, "result": {"platform": "youtube", "title": "Y",
             "public_url": "u", "duration_secs": 1}}], "listing_errors": [], "summary": "s"},
            {"enabled": True, "whisper_model": "base", "notify": False},
            run_fn=lambda cmd, **kw: calls.append(cmd) or _Proc(stdout="/o/y.md", returncode=0),
            send_fn=lambda *a: None)
        self.assertIn("--whisper-model", calls[0]); self.assertIn("base", calls[0])

    def test_summarize_action_omits_whisper_model_when_unset(self):
        calls = []
        act.summarize_action(
            {"outcomes": [{"ok": True, "result": {"platform": "youtube", "title": "Y",
             "public_url": "u", "duration_secs": 1}}], "listing_errors": [], "summary": "s"},
            {"enabled": True, "notify": False},
            run_fn=lambda cmd, **kw: calls.append(cmd) or _Proc(stdout="/o/y.md", returncode=0),
            send_fn=lambda *a: None)
        self.assertNotIn("--whisper-model", calls[0])

    def test_summarize_action_runs_in_cwd(self):
        cwds = []
        act.summarize_action(
            {"outcomes": [{"ok": True, "result": {"platform": "youtube", "title": "Y",
             "public_url": "u", "duration_secs": 1}}], "listing_errors": [], "summary": "s"},
            {"enabled": True, "cwd": "/proj/video-summarizer", "notify": False},
            run_fn=lambda cmd, **kw: cwds.append(kw.get("cwd")) or _Proc(stdout="/o/y.md", returncode=0),
            send_fn=lambda *a: None)
        self.assertEqual(cwds, ["/proj/video-summarizer"])

    def test_summarize_action_default_cwd_none(self):
        cwds = []
        act.summarize_action(
            {"outcomes": [{"ok": True, "result": {"platform": "youtube", "title": "Y",
             "public_url": "u", "duration_secs": 1}}], "listing_errors": [], "summary": "s"},
            {"enabled": True, "notify": False},
            run_fn=lambda cmd, **kw: cwds.append(kw.get("cwd")) or _Proc(stdout="/o/y.md", returncode=0),
            send_fn=lambda *a: None)
        self.assertEqual(cwds, [None])

    def test_summarize_action_passes_summary_backend_and_model(self):
        calls = []
        act.summarize_action(
            {"outcomes": [{"ok": True, "result": {"platform": "youtube", "title": "Y",
             "public_url": "u", "duration_secs": 1}}], "listing_errors": [], "summary": "s"},
            {"enabled": True, "summary_backend": "claude",
             "summary_model": "claude-opus-4-8", "notify": False},
            run_fn=lambda cmd, **kw: calls.append(cmd) or _Proc(stdout="/o/y.md", returncode=0),
            send_fn=lambda *a: None)
        self.assertIn("--summary-backend", calls[0])
        self.assertEqual(calls[0][calls[0].index("--summary-backend") + 1], "claude")
        self.assertIn("--summary-model", calls[0])
        self.assertEqual(calls[0][calls[0].index("--summary-model") + 1], "claude-opus-4-8")

    def test_summarize_action_omits_backend_flags_when_unset(self):
        # Default: pass nothing → the CLI uses its own default (Gemini), keeping the
        # live watcher's GEMINI_API_KEY path unchanged.
        calls = []
        act.summarize_action(
            {"outcomes": [{"ok": True, "result": {"platform": "youtube", "title": "Y",
             "public_url": "u", "duration_secs": 1}}], "listing_errors": [], "summary": "s"},
            {"enabled": True, "notify": False},
            run_fn=lambda cmd, **kw: calls.append(cmd) or _Proc(stdout="/o/y.md", returncode=0),
            send_fn=lambda *a: None)
        self.assertNotIn("--summary-backend", calls[0])
        self.assertNotIn("--summary-model", calls[0])

    def _parallel_probe(self, cap, n_items):
        """A fake run_fn that records peak concurrency. A `threading.Barrier(cap)`
        forces `cap` workers to overlap (proving the pool reaches the cap), while the
        pool's own bound keeps peak from exceeding it — so a correct implementation
        yields state["max"] == cap exactly. A serial implementation never fills the
        barrier: it times out, peak stays 1, and the caller's assertion fails."""
        import threading
        barrier = threading.Barrier(cap, timeout=3)
        lock = threading.Lock()
        state = {"now": 0, "max": 0}

        def fake_run(cmd, **kw):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            with lock:
                state["now"] -= 1
            return _Proc(stdout="/o/x.md\n", returncode=0)

        ctx = {"outcomes": [{"ok": True, "result": {"platform": "p", "title": f"T{i}",
                "public_url": f"u{i}", "duration_secs": 1}} for i in range(n_items)],
               "listing_errors": [], "summary": "s"}
        return fake_run, state, ctx

    def test_summarize_action_runs_concurrently_up_to_max_workers(self):
        fake_run, state, ctx = self._parallel_probe(cap=3, n_items=6)
        out = act.summarize_action(ctx, {"enabled": True, "max_workers": 3, "notify": False},
                                   run_fn=fake_run, send_fn=lambda *a: None)
        self.assertEqual(out["summarized"], 6)
        self.assertEqual(state["max"], 3)  # reached the cap, never exceeded it

    def test_summarize_action_respects_lower_max_workers(self):
        fake_run, state, ctx = self._parallel_probe(cap=2, n_items=6)
        act.summarize_action(ctx, {"enabled": True, "max_workers": 2, "notify": False},
                             run_fn=fake_run, send_fn=lambda *a: None)
        self.assertEqual(state["max"], 2)

    def test_summarize_action_default_max_workers_is_3(self):
        fake_run, state, ctx = self._parallel_probe(cap=3, n_items=6)
        act.summarize_action(ctx, {"enabled": True, "notify": False},  # no max_workers
                             run_fn=fake_run, send_fn=lambda *a: None)
        self.assertEqual(state["max"], 3)

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

    def test_default_max_items_is_10(self):
        self.assertEqual(w.parse_config('')["max_items"], 10)

    def test_default_concurrency_and_fragments(self):
        cfg = w.parse_config('')
        self.assertEqual(cfg["concurrency"], 5)
        self.assertEqual(cfg["concurrent_fragments"], 4)

    def test_validate_rejects_unknown_platform(self):
        cfg = w.parse_config('[platforms.vimeo]\nsource = "watch_later"\n')
        with self.assertRaises(ValueError):
            w.validate_config(cfg)

    def test_validate_rejects_action_without_name(self):
        cfg = w.parse_config('[[actions]]\nenabled = true\n')
        with self.assertRaises(ValueError):
            w.validate_config(cfg)

    def test_parse_config_platforms_override_is_wholesale(self):
        # Documented behavior: naming any platform replaces the whole platforms table,
        # so listing only youtube means bilibili is NOT polled.
        cfg = w.parse_config('[platforms.youtube]\nsource = "watch_later"\n')
        self.assertEqual(list(cfg["platforms"]), ["youtube"])
        self.assertNotIn("bilibili", cfg["platforms"])

    def test_validate_expands_state_path(self):
        cfg = w.parse_config('state_path = "~/foo/state.json"')
        w.validate_config(cfg)
        self.assertFalse(cfg["state_path"].startswith("~"))

    def test_default_config_has_post_run(self):
        cfg = w.parse_config("")
        names = [a.get("name") for a in cfg["post_run"]]
        self.assertEqual(names, ["notify", "mytv", "summarize"])
        summarize = [a for a in cfg["post_run"] if a["name"] == "summarize"][0]
        self.assertFalse(summarize["enabled"])
        self.assertEqual(summarize["command"], "video-summarizer")
        self.assertEqual(summarize["out"], "~/video-analyses")
        self.assertFalse(summarize["visual"])
        self.assertEqual(summarize["max_workers"], 3)


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

    def test_run_publish_forwards_engine_stderr(self):
        import contextlib
        import io
        env = {"ok": 1, "failed": 0,
               "results": [{"public_url": "u", "duration_secs": 1, "title": "t"}]}

        def fake_run(cmd, capture_output, text):
            return FakeProc(stdout=json.dumps(env), stderr="ERROR: real yt-dlp reason\n")

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            w.run_publish("URL", "/p.py", False, "chrome", run_fn=fake_run)
        self.assertIn("ERROR: real yt-dlp reason", buf.getvalue())

    def test_first_result(self):
        self.assertEqual(w.first_result({"results": [{"a": 1}]}), {"a": 1})
        self.assertIsNone(w.first_result({"results": []}))

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


def _base_deps(overrides):
    """Fully-faked deps for tick/process_entry — no network, subprocess, or fs."""
    deps = {
        "list_entries": lambda platform, source, cookies, max_items=None: [],
        "publish": lambda url, script, transcode, cookies, concurrent_fragments=1: {
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

    def test_process_entry_logs_published_url(self):
        entry = {"platform": "youtube", "id": "abc", "url": "u", "title": "t"}
        cfg = w.parse_config('')
        msgs = []
        w.process_entry(entry, cfg, "/p.py", _base_deps({}), log=msgs.append)
        self.assertTrue(any("https://b/x.mp4" in m for m in msgs))

    def test_process_entry_passes_fragments_to_publish(self):
        entry = {"platform": "youtube", "id": "a", "url": "u", "title": "t"}
        cfg = w.parse_config('')   # concurrent_fragments defaults to 4
        got = {}

        def publish(url, script, transcode, cookies, concurrent_fragments=1):
            got["frag"] = concurrent_fragments
            return {"results": [{"public_url": "x", "duration_secs": 1, "title": "t"}]}

        w.process_entry(entry, cfg, "/p.py", _base_deps({"publish": publish}), log=lambda m: None)
        self.assertEqual(got["frag"], 4)

    def test_process_entry_publish_error_skips_actions(self):
        entry = {"platform": "youtube", "id": "abc", "url": "u", "title": "t"}
        cfg = w.parse_config('')
        ran = {"called": False}
        deps = _base_deps({
            "publish": lambda *a, **k: {"results": [{"error": "download failed"}]},
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

        def publish(url, script, transcode, cookies, concurrent_fragments=1):
            if url == "u2":
                return {"results": [{"error": "boom"}]}
            return {"results": [{"public_url": "https://b/x.mp4", "duration_secs": 5, "title": "T"}]}

        cfg = w.parse_config('[platforms.youtube]\nsource = "watch_later"\n')
        cfg["platforms"] = {"youtube": {"source": "watch_later"}}  # single platform
        deps = _base_deps({
            "list_entries": lambda *a, **k: entries,
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

        def list_entries(platform, source, cookies, max_items=None):
            if platform == "youtube":
                raise RuntimeError("yt listing down")
            return [{"platform": "bilibili", "id": "b1", "url": "u", "title": "t"}]

        def publish(*a, **k):
            published["count"] += 1
            return {"results": [{"public_url": "https://b/x.mp4", "duration_secs": 1, "title": "T"}]}

        deps = _base_deps({"list_entries": list_entries, "publish": publish})
        result = w.tick(cfg, "/p.py", deps, log=lambda m: None)
        self.assertEqual(published["count"], 1)  # bilibili still processed despite youtube failing
        self.assertEqual(result["listing_errors"], ["youtube"])  # failed platform captured
        self.assertEqual(len(result["outcomes"]), 1)

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
        self.assertEqual(len(handled["outcomes"]), 3)
        self.assertTrue(all(o["ok"] for o in handled["outcomes"]))
        self.assertEqual(handled["listing_errors"], [])
        self.assertEqual(saved["keys"], {"youtube:v0", "youtube:v1", "youtube:v2"})

    def test_format_summary_counts(self):
        result = {"outcomes": [{"ok": True}, {"ok": True}, {"ok": False}], "listing_errors": []}
        self.assertEqual(w.format_summary(result), "run done: 2 published, 1 failed")

    def test_format_summary_idle(self):
        self.assertEqual(w.format_summary({"outcomes": [], "listing_errors": []}),
                         "run done: 0 published, 0 failed")

    def test_format_summary_one_listing_error(self):
        result = {"outcomes": [{"ok": True}], "listing_errors": ["youtube"]}
        self.assertEqual(w.format_summary(result),
                         "run done: 1 published, 0 failed · 1 listing error")

    def test_format_summary_two_listing_errors(self):
        result = {"outcomes": [], "listing_errors": ["youtube", "bilibili"]}
        self.assertEqual(w.format_summary(result),
                         "run done: 0 published, 0 failed · 2 listing errors")

    def test_tick_contains_worker_exception_and_others_continue(self):
        entries = [
            {"platform": "youtube", "id": "boom", "url": "boom_url", "title": "t"},
            {"platform": "youtube", "id": "good", "url": "good_url", "title": "t"},
        ]

        def publish(url, script, transcode, cookies, concurrent_fragments=1):
            if url == "boom_url":
                raise RuntimeError("publish exploded")
            return {"results": [{"public_url": "https://b/x.mp4", "duration_secs": 5, "title": "T"}]}

        saved = {"keys": set()}
        cfg = w.parse_config('')
        cfg["platforms"] = {"youtube": {"source": "watch_later"}}
        deps = _base_deps({
            "list_entries": lambda *a, **k: entries,
            "publish": publish,
            "save_state": lambda path, keys: saved.update(keys=set(keys)),
        })
        handled = w.tick(cfg, "/p.py", deps, log=lambda m: None)
        by_id = {o["entry"]["id"]: o for o in handled["outcomes"]}
        self.assertFalse(by_id["boom"]["ok"])           # failure contained, not raised
        self.assertTrue(by_id["good"]["ok"])            # other worker still ran
        self.assertEqual(saved["keys"], {"youtube:good"})  # only the success recorded


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


class Cli(unittest.TestCase):
    def test_build_deps_has_real_callables(self):
        deps = w.build_deps()
        for key in ("list_entries", "publish", "run_actions", "load_state",
                    "save_state", "new_entries", "entry_key", "run_post_run"):
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

    def test_parse_args_limit(self):
        self.assertIsNone(w.parse_args([]).limit)
        self.assertEqual(w.parse_args(["--limit", "3"]).limit, 3)

    def test_parse_args_concurrency(self):
        self.assertIsNone(w.parse_args([]).concurrency)
        self.assertEqual(w.parse_args(["--concurrency", "2"]).concurrency, 2)

    def test_select_platforms_all_when_none(self):
        cfg = w.parse_config('')
        self.assertEqual(set(w.select_platforms(cfg, None)), {"youtube", "bilibili"})

    def test_select_platforms_single(self):
        cfg = w.parse_config('')
        self.assertEqual(list(w.select_platforms(cfg, "youtube")), ["youtube"])

    def test_select_platforms_not_configured_raises(self):
        # Wholesale merge: this config has only youtube, so requesting bilibili must raise.
        cfg = w.parse_config('[platforms.youtube]\nsource = "watch_later"\n')
        with self.assertRaises(ValueError):
            w.select_platforms(cfg, "bilibili")


if __name__ == "__main__":
    unittest.main()
