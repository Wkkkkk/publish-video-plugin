import unittest

import publish_video as v


class Helpers(unittest.TestCase):
    def test_is_url(self):
        self.assertTrue(v.is_url("https://x/y.mp4"))
        self.assertTrue(v.is_url("http://x"))
        self.assertFalse(v.is_url("./movie.mp4"))
        self.assertFalse(v.is_url("/tmp/a.mp4"))

    def test_sanitize_filename(self):
        self.assertEqual(v.sanitize_filename("my movie!.mp4"), "my_movie_.mp4")
        # A slash in a TITLE must become "_", not truncate the title (no basename).
        self.assertEqual(v.sanitize_filename("a/b series"), "a_b_series")

    def test_object_key(self):
        self.assertEqual(v.object_key("vod", "a b.mp4", "ID"), "vod/ID-a_b.mp4")
        self.assertEqual(v.object_key("", "a.mp4", "ID"), "ID-a.mp4")
        self.assertEqual(v.object_key("/p/", "a.mp4", "ID"), "p/ID-a.mp4")

    def test_sanitize_filename_drops_non_ascii(self):
        # Non-ASCII (CJK etc.) is dropped so object keys / public URLs stay ASCII —
        # no percent-encoded Chinese in the URL. The video-id keeps the key unique.
        self.assertEqual(v.sanitize_filename("解析 OpenAI 实用 Agent"), "OpenAI_Agent")
        self.assertEqual(v.sanitize_filename("【Tech Podcast】别被AI忽悠了"), "Tech_Podcast_AI")
        self.assertEqual(v.sanitize_filename("这到底是“胃”什么呢"), "video")  # all CJK -> fallback
        self.assertEqual(v.sanitize_filename("a！！！b"), "a_b")  # collapse runs
        self.assertEqual(v.sanitize_filename("___edge___"), "edge")  # trim edges
        self.assertEqual(v.sanitize_filename(""), "video")  # never empty

    def test_detect_platform(self):
        self.assertEqual(v.detect_platform("https://www.youtube.com/watch?v=abc"), "youtube")
        self.assertEqual(v.detect_platform("https://youtu.be/abc"), "youtube")
        self.assertEqual(v.detect_platform("https://www.bilibili.com/video/BV1xx"), "bilibili")
        self.assertEqual(v.detect_platform("https://space.bilibili.com/9/favlist"), "bilibili")
        self.assertEqual(v.detect_platform("/local/file.mp4"), "local")

    def test_extract_video_id(self):
        self.assertEqual(v.extract_video_id("https://www.bilibili.com/video/BV12rJA66EBW"), "BV12rJA66EBW")
        self.assertEqual(v.extract_video_id("https://www.youtube.com/watch?v=2n41YjR5QfU"), "2n41YjR5QfU")
        self.assertEqual(v.extract_video_id("https://youtu.be/2n41YjR5QfU"), "2n41YjR5QfU")
        self.assertIsNone(v.extract_video_id("https://example.com/clip.mp4"))

    def test_source_tag(self):
        self.assertEqual(
            v.source_tag("https://www.bilibili.com/video/BV12rJA66EBW", today="20260615"),
            "bilibili-20260615-BV12rJA66EBW")
        tag = v.source_tag("https://example.com/x.mp4", today="20260615")
        self.assertTrue(tag.startswith("example-20260615-"))  # id falls back to a short hash

    def test_public_url_encodes_unicode(self):
        url = v.public_url("https://b.r2.dev", "video/bilibili-20260615-BV1-解析.mp4")
        self.assertTrue(url.startswith("https://b.r2.dev/video/bilibili-20260615-BV1-"))
        self.assertNotIn("解析", url)  # CJK percent-encoded
        self.assertIn("%", url)

    def test_build_ytdlp_cmd_with_cookies(self):
        cmd = v.build_ytdlp_cmd("URL", "/tmp/o.mp4", "chrome", "vcodec:h264,acodec:aac")
        self.assertEqual(cmd[0], "yt-dlp")
        self.assertIn("--cookies-from-browser", cmd)
        self.assertIn("chrome", cmd)
        self.assertIn("bv*[vcodec~=avc1]+ba[ext=m4a]/b[vcodec~=avc1]", cmd)
        self.assertEqual(cmd[-1], "URL")  # url after the "--" guard

    def test_build_ytdlp_cmd_without_cookies(self):
        cmd = v.build_ytdlp_cmd("URL", "/tmp/o.mp4", None, "vcodec:h264")
        self.assertNotIn("--cookies-from-browser", cmd)

    def test_build_ytdlp_cmd_concurrent_fragments(self):
        cmd = v.build_ytdlp_cmd("URL", "/o.mp4", None, "vcodec:h264", concurrent_fragments=4)
        self.assertIn("-N", cmd)
        self.assertIn("4", cmd)

    def test_build_ytdlp_cmd_no_fragments_when_one(self):
        cmd = v.build_ytdlp_cmd("URL", "/o.mp4", None, "vcodec:h264", concurrent_fragments=1)
        self.assertNotIn("-N", cmd)

    def test_build_ytdlp_cmd_js_runtime_default(self):
        # YouTube's signature/n-challenge needs an enabled JS runtime (yt-dlp only
        # enables deno by default); we opt in to node by default.
        cmd = v.build_ytdlp_cmd("URL", "/o.mp4", None, "vcodec:h264")
        self.assertIn("--js-runtimes", cmd)
        self.assertEqual(cmd[cmd.index("--js-runtimes") + 1], "node")
        self.assertEqual(cmd[-1], "URL")  # flags stay before the "--" guard

    def test_build_ytdlp_cmd_remote_components_default(self):
        # The EJS challenge-solver script is fetched from yt-dlp's GitHub releases.
        cmd = v.build_ytdlp_cmd("URL", "/o.mp4", None, "vcodec:h264")
        self.assertIn("--remote-components", cmd)
        self.assertEqual(cmd[cmd.index("--remote-components") + 1], "ejs:github")

    def test_build_ytdlp_cmd_js_runtime_omitted_when_empty(self):
        cmd = v.build_ytdlp_cmd("URL", "/o.mp4", None, "vcodec:h264", js_runtimes="")
        self.assertNotIn("--js-runtimes", cmd)

    def test_build_ytdlp_cmd_remote_components_omitted_when_empty(self):
        cmd = v.build_ytdlp_cmd("URL", "/o.mp4", None, "vcodec:h264", remote_components="")
        self.assertNotIn("--remote-components", cmd)

    def test_build_title_cmd_includes_js_runtime(self):
        # YouTube title extraction (with cookies) does full format extraction, which now
        # needs an enabled JS runtime — without it yt-dlp errors and the title is lost.
        cmd = v.build_title_cmd("URL", "chrome")
        self.assertIn("--print", cmd)
        self.assertIn("--js-runtimes", cmd)
        self.assertEqual(cmd[cmd.index("--js-runtimes") + 1], "node")
        self.assertIn("--cookies-from-browser", cmd)
        self.assertEqual(cmd[-1], "URL")  # url after the "--" guard

    def test_build_title_cmd_omits_flags_when_empty(self):
        cmd = v.build_title_cmd("URL", None, js_runtimes="", remote_components="")
        self.assertNotIn("--js-runtimes", cmd)
        self.assertNotIn("--remote-components", cmd)
        self.assertNotIn("--cookies-from-browser", cmd)

    def test_build_ytdlp_cmd_no_progress(self):
        cmd = v.build_ytdlp_cmd("URL", "/o.mp4", None, "vcodec:h264")
        self.assertIn("--no-progress", cmd)
        self.assertEqual(cmd[-1], "URL")  # flags stay before the "--" guard

    def test_build_register_url(self):
        self.assertEqual(
            v.build_register_url("https://h.fly.dev/", 7),
            "https://h.fly.dev/api/admin/channels/7/playlist",
        )

    def test_build_payload(self):
        self.assertEqual(
            v.build_payload("T", "U", 5),
            {"title": "T", "url": "U", "duration_secs": 5},
        )

    def test_public_url(self):
        self.assertEqual(
            v.public_url("https://b.r2.dev/", "/k/x.mp4"),
            "https://b.r2.dev/k/x.mp4",
        )

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
        self.assertEqual(created, [])

    def test_ensure_channel_creates_when_missing(self):
        created = []
        def fake_create(base, password, name, category, channel_type):
            created.append((name, category, channel_type)); return {"id": 42}
        cid = v.ensure_channel("https://tv", "pw", "MyBilibili", "saved", "vod_on_demand",
                               existing=[{"id": 7, "name": "MyYoutube"}], create_fn=fake_create)
        self.assertEqual(cid, 42)
        self.assertEqual(created, [("MyBilibili", "saved", "vod_on_demand")])


class Errors(unittest.TestCase):
    def test_publisherror_is_exception(self):
        self.assertTrue(issubclass(v.PublishError, Exception))

    def test_publisherror_carries_message(self):
        err = v.PublishError("boom")
        self.assertEqual(str(err), "boom")


class Classify(unittest.TestCase):
    def test_has_media_ext(self):
        self.assertTrue(v.has_media_ext("https://x/y.MP4?token=1"))
        self.assertTrue(v.has_media_ext("https://x/y.webm"))
        self.assertFalse(v.has_media_ext("https://x/watch?v=abc"))
        self.assertFalse(v.has_media_ext("https://x/y.m3u8"))

    def test_is_video_file(self):
        self.assertTrue(v.is_video_file("Ep1.mkv"))
        self.assertTrue(v.is_video_file("a.MP4"))
        self.assertFalse(v.is_video_file("notes.txt"))

    def test_classify_local(self):
        isdir = lambda p: p == "/movies"
        isfile = lambda p: p == "/movies/a.mp4"
        self.assertEqual(v.classify_source("/movies", isdir, isfile), "directory")
        self.assertEqual(v.classify_source("/movies/a.mp4", isdir, isfile), "local_file")

    def test_classify_urls(self):
        no = lambda p: False
        self.assertEqual(v.classify_source("https://x/y.mp4", no, no), "direct_url")
        self.assertEqual(v.classify_source("https://youtu.be/abc", no, no), "ytdlp_url")

    def test_classify_unknown_raises(self):
        no = lambda p: False
        with self.assertRaises(ValueError):
            v.classify_source("./missing.mp4", no, no)


class Resolve(unittest.TestCase):
    def test_parse_source_list(self):
        text = "https://a/x.mp4\n# comment\n\n  ./b.mp4  \n"
        self.assertEqual(v.parse_source_list(text), ["https://a/x.mp4", "./b.mp4"])

    def test_expand_directory(self):
        listing = {"/m": ["a.mp4", "b.txt", "c.mkv"]}
        walk = lambda p: [(p, [], listing[p])]
        got = v.expand_directory("/m", recursive=False, walk_fn=walk)
        self.assertEqual(got, ["/m/a.mp4", "/m/c.mkv"])

    def test_resolve_jobs_expands_dir(self):
        classify = lambda s, *_: {"/m": "directory", "/m/a.mp4": "local_file",
                                  "https://x/y.mp4": "direct_url"}[s]
        walk = lambda p: [("/m", [], ["a.mp4"])]
        jobs = v.resolve_jobs(["/m", "https://x/y.mp4"], recursive=False,
                              classify_fn=classify, walk_fn=walk)
        self.assertEqual(jobs, [("/m/a.mp4", "local_file"),
                                ("https://x/y.mp4", "direct_url")])

    def test_required_tools(self):
        jobs = [("u", "ytdlp_url"), ("f", "local_file")]
        self.assertEqual(v.required_tools(jobs, transcode=False), {"ffprobe", "yt-dlp"})
        self.assertEqual(v.required_tools([("f", "local_file")], transcode=True),
                         {"ffprobe", "ffmpeg"})


class Acquire(unittest.TestCase):
    def test_content_type_for(self):
        self.assertEqual(v.content_type_for("/x/a.mp4"), "video/mp4")
        self.assertEqual(v.content_type_for("/x/a.webm"), "video/webm")
        self.assertEqual(v.content_type_for("/x/a.mov"), "video/quicktime")
        self.assertEqual(v.content_type_for("/x/a.unknown"), "video/mp4")

    def test_build_ffmpeg_transcode_cmd(self):
        cmd = v.build_ffmpeg_transcode_cmd("/in.mkv", "/out.mp4")
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("libx264", cmd)
        self.assertIn("aac", cmd)
        self.assertEqual(cmd[-1], "/out.mp4")


class Playable(unittest.TestCase):
    def test_playable_true(self):
        self.assertTrue(v.is_browser_playable("mp4", "h264", "aac"))
        self.assertTrue(v.is_browser_playable("mp4", "h264", ""))   # no audio
        self.assertTrue(v.is_browser_playable("mp4", "h264", None))

    def test_playable_false(self):
        self.assertFalse(v.is_browser_playable("webm", "vp9", "opus"))
        self.assertFalse(v.is_browser_playable("mp4", "av1", "aac"))
        self.assertFalse(v.is_browser_playable("mkv", "h264", "aac"))


class Results(unittest.TestCase):
    def test_build_result(self):
        r = v.build_result("src", "local_file", "T", "https://b/k.mp4", "k.mp4", 12, True, False)
        self.assertEqual(r["public_url"], "https://b/k.mp4")
        self.assertEqual(r["duration_secs"], 12)
        self.assertTrue(r["passthrough"])
        self.assertNotIn("error", r)

    def test_error_result(self):
        r = v.error_result("src", "ytdlp_url", "boom")
        self.assertEqual(r["error"], "boom")
        self.assertNotIn("public_url", r)

    def test_envelope_and_exit(self):
        ok = v.build_result("s", "local_file", "T", "u", "k", 1, True, False)
        bad = v.error_result("s2", "ytdlp_url", "x")
        env = v.build_envelope([ok, bad])
        self.assertEqual(env["ok"], 1)
        self.assertEqual(env["failed"], 1)
        self.assertEqual(env["results"], [ok, bad])
        self.assertEqual(v.exit_code_for([ok, bad]), 1)
        self.assertEqual(v.exit_code_for([ok]), 0)

    def test_derive_title_dry_run(self):
        self.assertEqual(v.derive_title("/x/My Clip.mkv", "local_file", None,
                                        cookies=None, dry_run=True), "My Clip")
        self.assertEqual(v.derive_title("https://x/y.mp4", "direct_url", None,
                                        cookies=None, dry_run=True), "y")
        self.assertEqual(v.derive_title("https://x/y.mp4", "direct_url", "Override",
                                        cookies=None, dry_run=True), "Override")


class DryRunPlan(unittest.TestCase):
    def test_plan_job_local(self):
        plan = v.plan_job("/x/My Clip.mp4", "local_file", key_prefix="video",
                          public_base="https://b", title_override=None, transcode=False,
                          uid="ID")
        self.assertEqual(plan["title"], "My Clip")
        self.assertEqual(plan["object_key"], "video/ID-My_Clip.mp4")
        self.assertEqual(plan["public_url"], "https://b/video/ID-My_Clip.mp4")
        self.assertTrue(plan["dry_run"])


if __name__ == "__main__":
    unittest.main()
