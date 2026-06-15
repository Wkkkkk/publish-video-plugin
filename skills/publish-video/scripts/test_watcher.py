import json
import os
import tempfile
import unittest

import watcher as w
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


if __name__ == "__main__":
    unittest.main()
