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


if __name__ == "__main__":
    unittest.main()
