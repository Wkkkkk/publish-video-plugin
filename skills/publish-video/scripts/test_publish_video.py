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
        self.assertEqual(v.sanitize_filename("/path/to/Ep 1.mp4"), "Ep_1.mp4")

    def test_object_key(self):
        self.assertEqual(v.object_key("vod", "a b.mp4", "ID"), "vod/ID-a_b.mp4")
        self.assertEqual(v.object_key("", "a.mp4", "ID"), "ID-a.mp4")
        self.assertEqual(v.object_key("/p/", "a.mp4", "ID"), "p/ID-a.mp4")

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
