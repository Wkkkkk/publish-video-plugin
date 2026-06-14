#!/usr/bin/env python3
"""Upload a local MP4 (or download+mux a video URL) to public object storage and
register it as a MyTV VOD playlist item via the JSON admin API.

Independent of the MyTV server. See scripts/README.md for prerequisites, env
vars, and usage.
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


def is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


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
        sys.exit("error: missing required env vars: " + ", ".join(missing))


def require_tool(name: str):
    if shutil.which(name) is None:
        sys.exit(f"error: required tool not found on PATH: {name}")


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
        sys.exit(f"error: ffprobe failed: {out.stderr.strip()}")
    try:
        return round(float(out.stdout.strip()))
    except ValueError:
        sys.exit(f"error: could not parse ffprobe duration: {out.stdout!r}")


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
        sys.exit("error: yt-dlp download/mux failed (see output above)")


def upload_to_bucket(path: str, endpoint: str, bucket: str, key: str):
    try:
        import boto3
    except ImportError:
        sys.exit("error: boto3 is required for upload (pip install boto3)")
    client = boto3.client("s3", endpoint_url=endpoint)
    client.upload_file(path, bucket, key, ExtraArgs={"ContentType": "video/mp4"})


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
        sys.exit(f"error: API returned {e.code}: {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"error: could not reach {url}: {e.reason}")


def main():
    p = argparse.ArgumentParser(
        description="Upload/download a video to object storage and register it as a MyTV VOD item."
    )
    p.add_argument("source", help="Local MP4 path OR a video URL (yt-dlp-supported, e.g. Bilibili/YouTube)")
    p.add_argument("--channel", type=int, required=True, help="MyTV channel id to add the item to")
    p.add_argument("--title", help="Item title (default: yt-dlp title for URLs, filename for local files)")
    p.add_argument("--key-prefix", default="vod", help="Object key prefix (default: vod)")
    p.add_argument("--cookies-from-browser", dest="cookies", default="chrome",
                   help="Browser for yt-dlp cookies on URL sources (default: chrome)")
    p.add_argument("--format-sort", default="vcodec:h264,acodec:aac",
                   help="yt-dlp -S sort string (default prefers H.264/AAC)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print planned actions without downloading, uploading, or registering")
    args = p.parse_args()

    require_env("MYTV_BASE_URL", "MYTV_ADMIN_PASSWORD",
                "VOD_S3_ENDPOINT", "VOD_S3_BUCKET", "VOD_PUBLIC_BASE_URL")
    base = os.environ["MYTV_BASE_URL"]
    password = os.environ["MYTV_ADMIN_PASSWORD"]
    endpoint = os.environ["VOD_S3_ENDPOINT"]
    bucket = os.environ["VOD_S3_BUCKET"]
    public_base = os.environ["VOD_PUBLIC_BASE_URL"]

    remote = is_url(args.source)
    if remote:
        # In a dry run, avoid spawning yt-dlp just to learn the title.
        title = args.title or (None if args.dry_run else fetch_title(args.source, args.cookies)) or "Untitled"
        filename = sanitize_filename(title) + ".mp4"
    else:
        if not os.path.isfile(args.source):
            sys.exit(f"error: file not found: {args.source}")
        title = args.title or os.path.splitext(os.path.basename(args.source))[0]
        filename = os.path.basename(args.source)

    key = object_key(args.key_prefix, filename, uuid.uuid4().hex)
    final_url = public_url(public_base, key)

    if args.dry_run:
        print(json.dumps({
            "dry_run": True,
            "source": args.source,
            "title": title,
            "object_key": key,
            "public_url": final_url,
            "register_url": build_register_url(base, args.channel),
        }, indent=2))
        return

    tmp = None
    if remote:
        require_tool("yt-dlp")
        require_tool("ffprobe")
        tmp = tempfile.mkdtemp(prefix="vod_upload_")
        local_path = os.path.join(tmp, "video.mp4")
    else:
        require_tool("ffprobe")
        local_path = args.source

    try:
        if remote:
            download_and_mux(args.source, local_path, args.cookies, args.format_sort)
        duration = probe_duration(local_path)
        upload_to_bucket(local_path, endpoint, bucket, key)
        item = register_item(base, args.channel, password,
                             build_payload(title, final_url, duration))
        print(json.dumps({"registered": item, "public_url": final_url}, indent=2))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
