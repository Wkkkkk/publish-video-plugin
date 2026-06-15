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
        entry = {"platform": "youtube", "id": "abc", "url": "u", "title": "fallback"}
        published = {"public_url": "https://b/x.mp4", "duration_secs": 9, "title": "Real"}
        r = w.make_result(entry, published)
        self.assertEqual(r, {"platform": "youtube", "source_id": "abc", "title": "Real",
                             "public_url": "https://b/x.mp4", "duration_secs": 9})


def _base_deps(overrides):
    """Fully-faked deps for tick/process_entry — no network, subprocess, or fs."""
    deps = {
        "list_entries": lambda platform, source, cookies, max_items=None: [],
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

    def test_process_entry_logs_published_url(self):
        entry = {"platform": "youtube", "id": "abc", "url": "u", "title": "t"}
        cfg = w.parse_config('')
        msgs = []
        w.process_entry(entry, cfg, "/p.py", _base_deps({}), log=msgs.append)
        self.assertTrue(any("https://b/x.mp4" in m for m in msgs))

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

        def publish(*a):
            published["count"] += 1
            return {"results": [{"public_url": "https://b/x.mp4", "duration_secs": 1, "title": "T"}]}

        deps = _base_deps({"list_entries": list_entries, "publish": publish})
        w.tick(cfg, "/p.py", deps, log=lambda m: None)
        self.assertEqual(published["count"], 1)  # bilibili still processed despite youtube failing


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
