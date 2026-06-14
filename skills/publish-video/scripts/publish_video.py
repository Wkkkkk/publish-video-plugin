#!/usr/bin/env python3
"""Publish a video to a public URL: take a local file, a direct media URL, a
yt-dlp-supported site URL, or a folder of videos, normalize it to a
browser-playable H.264/AAC MP4, upload it to S3-compatible object storage, and
print a JSON result envelope on stdout.

Standalone — no server dependency. Registering the result as a MyTV VOD playlist
item is one optional sink (--sink mytv). See ../SKILL.md and ../REFERENCE.md for
prerequisites, env vars, flags, and usage.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid

# yt-dlp format selector: a video stream encoded with H.264 (avc1) merged with an
# m4a audio stream, falling back to any avc1 combined format. Forces a
# browser-playable MP4 (Bilibili's default "best" is AV1, which Safari can't play).
AVC1_FORMAT = "bv*[vcodec~=avc1]+ba[ext=m4a]/b[vcodec~=avc1]"


class PublishError(Exception):
    """A per-item failure; caught by the batch loop so other items continue."""


def die(msg: str):
    """Config/usage error: print to stderr and exit 2."""
    print(msg, file=sys.stderr)
    sys.exit(2)


def is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


MEDIA_EXTS = (".mp4", ".webm", ".mov", ".m4v")
VIDEO_FILE_EXTS = (".mp4", ".webm", ".mov", ".m4v", ".mkv", ".avi")


def has_media_ext(url: str, exts=MEDIA_EXTS) -> bool:
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    return path.endswith(exts)


def is_video_file(name: str, exts=VIDEO_FILE_EXTS) -> bool:
    return name.lower().endswith(exts)


def classify_source(source: str, isdir=os.path.isdir, isfile=os.path.isfile) -> str:
    if isdir(source):
        return "directory"
    if isfile(source):
        return "local_file"
    if is_url(source):
        return "direct_url" if has_media_ext(source) else "ytdlp_url"
    raise ValueError(f"not a file, directory, or URL: {source}")


def parse_source_list(text: str) -> list:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def expand_directory(path: str, recursive: bool, walk_fn=os.walk) -> list:
    files = []
    for root, _dirs, names in walk_fn(path):
        for name in sorted(names):
            if is_video_file(name):
                files.append(os.path.join(root, name))
        if not recursive:
            break
    return files


def resolve_jobs(sources, recursive, classify_fn=classify_source, walk_fn=os.walk) -> list:
    jobs = []
    for source in sources:
        stype = classify_fn(source)
        if stype == "directory":
            for f in expand_directory(source, recursive, walk_fn):
                jobs.append((f, "local_file"))
        else:
            jobs.append((source, stype))
    return jobs


def required_tools(jobs, transcode: bool) -> set:
    tools = {"ffprobe"}
    if any(t == "ytdlp_url" for _, t in jobs):
        tools.add("yt-dlp")
    if transcode:
        tools.add("ffmpeg")
    return tools


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(name))


def object_key(prefix: str, filename: str, uid: str) -> str:
    safe = sanitize_filename(filename)
    p = prefix.strip("/")
    return f"{p}/{uid}-{safe}" if p else f"{uid}-{safe}"


def build_ytdlp_cmd(url: str, out_path: str, cookies_from_browser, format_sort: str):
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
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    cmd += ["--", url]
    return cmd


def build_register_url(base: str, channel: int) -> str:
    return f"{base.rstrip('/')}/api/admin/channels/{channel}/playlist"


def build_payload(title: str, url: str, duration_secs: int) -> dict:
    return {"title": title, "url": url, "duration_secs": duration_secs}


def public_url(base: str, key: str) -> str:
    return f"{base.rstrip('/')}/{key.lstrip('/')}"


def require_env(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        die("error: missing required env vars: " + ", ".join(missing))


def require_tool(name: str):
    if shutil.which(name) is None:
        die(f"error: required tool not found on PATH: {name}")


def probe_duration(path: str) -> int:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nokey=1:noprint_wrappers=1",
            path,
        ],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise PublishError(f"ffprobe failed: {out.stderr.strip()}")
    try:
        return round(float(out.stdout.strip()))
    except ValueError:
        raise PublishError(f"could not parse ffprobe duration: {out.stdout!r}")


def fetch_title(url: str, cookies) -> str | None:
    cmd = ["yt-dlp", "--no-playlist", "--print", "title"]
    if cookies:
        cmd += ["--cookies-from-browser", cookies]
    cmd += ["--", url]
    out = subprocess.run(cmd, capture_output=True, text=True)
    title = out.stdout.strip()
    return title if out.returncode == 0 and title else None


def download_and_mux(url: str, out_path: str, cookies, format_sort: str):
    cmd = build_ytdlp_cmd(url, out_path, cookies, format_sort)
    print("+ " + " ".join(cmd), file=sys.stderr)
    if subprocess.run(cmd).returncode != 0:
        raise PublishError("yt-dlp download/mux failed (see output above)")


CONTENT_TYPES = {
    "mp4": "video/mp4", "m4v": "video/x-m4v",
    "webm": "video/webm", "mov": "video/quicktime",
}


def content_type_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return CONTENT_TYPES.get(ext, "video/mp4")


def build_ffmpeg_transcode_cmd(in_path: str, out_path: str) -> list:
    return [
        "ffmpeg", "-y", "-i", in_path,
        "-c:v", "libx264", "-c:a", "aac",
        "-movflags", "+faststart", out_path,
    ]


def download_direct(url: str, out_path: str):
    try:
        with urllib.request.urlopen(url) as resp, open(out_path, "wb") as f:
            shutil.copyfileobj(resp, f)
    except (urllib.error.URLError, OSError) as e:
        raise PublishError(f"direct download failed for {url}: {e}")


def transcode_to_h264(in_path: str, out_path: str):
    if subprocess.run(build_ffmpeg_transcode_cmd(in_path, out_path)).returncode != 0:
        raise PublishError("ffmpeg transcode failed (see output above)")


def is_browser_playable(container: str, vcodec: str, acodec) -> bool:
    return container == "mp4" and vcodec == "h264" and (acodec in ("aac", "", None))


def probe_streams(path: str):
    def codec(kind: str) -> str:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", kind,
             "-show_entries", "stream=codec_name",
             "-of", "default=nokey=1:noprint_wrappers=1", path],
            capture_output=True, text=True,
        )
        return out.stdout.strip()
    return codec("v:0"), codec("a:0")


def ensure_playable(path: str, transcode: bool, workdir: str):
    container = os.path.splitext(path)[1].lstrip(".").lower()
    vcodec, acodec = probe_streams(path)
    if is_browser_playable(container, vcodec, acodec):
        return path, True, False
    if transcode:
        out = os.path.join(workdir, "transcoded.mp4")
        transcode_to_h264(path, out)
        return out, False, True
    print(
        f"warning: {os.path.basename(path)} is "
        f"{container}/{vcodec or '?'}/{acodec or 'no-audio'}; may not play in all browsers",
        file=sys.stderr,
    )
    return path, False, False


def acquire(source: str, stype: str, workdir: str, cookies, format_sort: str) -> str:
    if stype == "ytdlp_url":
        out = os.path.join(workdir, "video.mp4")
        download_and_mux(source, out, cookies, format_sort)
        return out
    if stype == "direct_url":
        name = sanitize_filename(os.path.basename(source.split("?", 1)[0])) or "video.mp4"
        out = os.path.join(workdir, name)
        download_direct(source, out)
        return out
    if stype == "local_file":
        return source
    raise PublishError(f"cannot acquire source type: {stype}")


def upload_to_bucket(path: str, endpoint: str, bucket: str, key: str, content_type: str = "video/mp4"):
    try:
        import boto3
    except ImportError:
        raise PublishError("boto3 is required for upload (pip install boto3)")
    client = boto3.client("s3", endpoint_url=endpoint)
    client.upload_file(path, bucket, key, ExtraArgs={"ContentType": content_type})


def build_result(source, stype, title, public, key, duration, passthrough, transcoded) -> dict:
    return {
        "source": source, "type": stype, "title": title,
        "public_url": public, "object_key": key, "duration_secs": duration,
        "passthrough": passthrough, "transcoded": transcoded,
    }


def error_result(source, stype, message) -> dict:
    return {"source": source, "type": stype, "error": message}


def build_envelope(results) -> dict:
    failed = sum(1 for r in results if "error" in r)
    return {"ok": len(results) - failed, "failed": failed, "results": results}


def exit_code_for(results) -> int:
    return 1 if any("error" in r for r in results) else 0


def derive_title(source, stype, override, cookies, dry_run) -> str:
    if override:
        return override
    if stype == "local_file":
        return os.path.splitext(os.path.basename(source))[0]
    if stype == "direct_url":
        base = os.path.basename(source.split("?", 1)[0])
        return os.path.splitext(base)[0] or "Untitled"
    # ytdlp_url
    if dry_run:
        return "Untitled"
    return fetch_title(source, cookies) or "Untitled"


def register_item(base: str, channel: int, password: str, payload: dict) -> dict:
    url = build_register_url(base, channel)
    token = base64.b64encode(f"user:{password}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {token}",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise PublishError(f"MyTV API returned {e.code}: {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        raise PublishError(f"could not reach {url}: {e.reason}")


def plan_job(source, stype, key_prefix, public_base, title_override, transcode, uid) -> dict:
    title = derive_title(source, stype, title_override, cookies=None, dry_run=True)
    ext = "mp4"
    if stype in ("direct_url", "local_file"):
        ext = os.path.splitext(source.split("?", 1)[0])[1].lstrip(".").lower() or "mp4"
        if transcode and ext != "mp4":
            ext = "mp4"
    key = object_key(key_prefix, sanitize_filename(title) + "." + ext, uid)
    return {
        "source": source, "type": stype, "title": title,
        "object_key": key, "public_url": public_url(public_base, key),
        "dry_run": True,
    }


def process_job(source, stype, args, endpoint, bucket, public_base) -> dict:
    workdir = tempfile.mkdtemp(prefix="publish_video_")
    try:
        acquired = acquire(source, stype, workdir, args.cookies, args.format_sort)
        final_path, passthrough, transcoded = ensure_playable(acquired, args.transcode, workdir)
        duration = probe_duration(final_path)
        title = derive_title(source, stype, args.title, args.cookies, dry_run=False)
        ext = os.path.splitext(final_path)[1].lstrip(".").lower() or "mp4"
        key = object_key(args.key_prefix, sanitize_filename(title) + "." + ext, uuid.uuid4().hex)
        upload_to_bucket(final_path, endpoint, bucket, key, content_type_for(final_path))
        return build_result(source, stype, title, public_url(public_base, key),
                            key, duration, passthrough, transcoded)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main():
    p = argparse.ArgumentParser(
        description="Publish a video (file, URL, yt-dlp site, or folder) to a public URL."
    )
    p.add_argument("sources", nargs="*", help="yt-dlp URL | direct media URL | local file | local directory")
    p.add_argument("--from-file", dest="from_file", help="read additional sources, one per line (# comments)")
    p.add_argument("--recursive", action="store_true", help="descend into subdirectories for directory sources")
    p.add_argument("--title", help="title override (single-source runs only)")
    p.add_argument("--key-prefix", default="video", help="object key prefix (default: video)")
    p.add_argument("--cookies-from-browser", dest="cookies", default="chrome",
                   help="browser for yt-dlp cookies (default: chrome; URL sources)")
    p.add_argument("--format-sort", default="vcodec:h264,acodec:aac",
                   help="yt-dlp -S string (default prefers H.264/AAC)")
    p.add_argument("--transcode", action="store_true",
                   help="re-encode non-H.264/AAC inputs (default: warn + upload as-is)")
    p.add_argument("--sink", choices=["print", "mytv"], default="print", help="output sink")
    p.add_argument("--channel", type=int, help="MyTV channel id (required with --sink mytv)")
    p.add_argument("--dry-run", action="store_true", help="print planned actions; no download/upload/register")
    args = p.parse_args()

    sources = list(args.sources)
    if args.from_file:
        try:
            with open(args.from_file) as f:
                sources += parse_source_list(f.read())
        except OSError as e:
            die(f"error: cannot read --from-file {args.from_file}: {e}")
    if not sources:
        die("error: no sources given (pass SOURCE args and/or --from-file)")
    if args.title and len(sources) > 1:
        die("error: --title only applies to a single source")
    if args.sink == "mytv" and args.channel is None:
        die("error: --sink mytv requires --channel")

    require_env("PUBLISH_VIDEO_S3_ENDPOINT", "PUBLISH_VIDEO_S3_BUCKET", "PUBLISH_VIDEO_PUBLIC_BASE_URL")
    endpoint = os.environ["PUBLISH_VIDEO_S3_ENDPOINT"]
    bucket = os.environ["PUBLISH_VIDEO_S3_BUCKET"]
    public_base = os.environ["PUBLISH_VIDEO_PUBLIC_BASE_URL"]

    try:
        jobs = resolve_jobs(sources, args.recursive)
    except ValueError as e:
        die(f"error: {e}")
    if not jobs:
        die("error: no video files found in the given sources")
    if args.title and len(jobs) > 1:
        die("error: --title only applies to a single video (this expanded to multiple)")

    if args.dry_run:
        results = [plan_job(s, t, args.key_prefix, public_base, args.title, args.transcode,
                            uuid.uuid4().hex) for s, t in jobs]
        print(json.dumps(build_envelope(results), indent=2))
        return

    for tool in sorted(required_tools(jobs, args.transcode)):
        require_tool(tool)
    if args.sink == "mytv":
        require_env("MYTV_BASE_URL", "MYTV_ADMIN_PASSWORD")

    results = []
    for source, stype in jobs:
        try:
            result = process_job(source, stype, args, endpoint, bucket, public_base)
            if args.sink == "mytv":
                item = register_item(os.environ["MYTV_BASE_URL"], args.channel,
                                     os.environ["MYTV_ADMIN_PASSWORD"],
                                     build_payload(result["title"], result["public_url"],
                                                   result["duration_secs"]))
                result["mytv_item"] = item.get("id", item)
        except PublishError as e:
            result = error_result(source, stype, str(e))
        results.append(result)

    print(json.dumps(build_envelope(results), indent=2))
    sys.exit(exit_code_for(results))


if __name__ == "__main__":
    main()
