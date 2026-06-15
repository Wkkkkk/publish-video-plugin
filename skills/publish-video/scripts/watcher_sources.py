"""List entries from a saved-video source (Watch Later or a playlist/folder URL)
via `yt-dlp --flat-playlist`. List-only — never downloads."""
from __future__ import annotations

import subprocess
import sys

# yt-dlp playlist URLs for each platform's Watch Later list. Verify against your
# installed yt-dlp once with real cookies — Bilibili's watchlater extractor URL
# has changed across yt-dlp versions.
WATCH_LATER = {
    "youtube": "https://www.youtube.com/playlist?list=WL",
    "bilibili": "https://www.bilibili.com/watchlater/#/list",
}

# Tab-separated so titles (which may contain spaces) survive a simple split.
PRINT_TEMPLATE = "%(id)s\t%(url)s\t%(title)s"


def source_to_url(platform: str, source: str) -> str:
    if source == "watch_later":
        try:
            return WATCH_LATER[platform]
        except KeyError:
            raise ValueError(f"no Watch Later URL known for platform: {platform}")
    if source.startswith("http://") or source.startswith("https://"):
        return source
    raise ValueError(f"source must be 'watch_later' or a full URL, got: {source!r}")


def build_list_cmd(url: str, cookies_browser) -> list:
    cmd = ["yt-dlp", "--flat-playlist", "--print", PRINT_TEMPLATE]
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    cmd += ["--", url]
    return cmd


def parse_listing(platform: str, stdout: str) -> list:
    entries = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        vid, url = parts[0], parts[1]
        title = parts[2] if len(parts) > 2 else ""
        entries.append({"platform": platform, "id": vid, "url": url, "title": title})
    return entries


def list_entries(platform, source, cookies_browser, run_fn=subprocess.run) -> list:
    url = source_to_url(platform, source)
    cmd = build_list_cmd(url, cookies_browser)
    print("+ " + " ".join(cmd), file=sys.stderr)
    proc = run_fn(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"yt-dlp listing failed for {platform} ({url}): {proc.stderr.strip()[:300]}"
        )
    return parse_listing(platform, proc.stdout)
