#!/usr/bin/env python3
"""Poll a saved-video source (Watch Later or a playlist/folder URL) on YouTube +
Bilibili, publish each new item via publish_video.py, then run config-driven
post-publish actions. Run with --once (single pass) or as a loop. See REFERENCE.md."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib

import watcher_actions
import watcher_sources
import watcher_state

KNOWN_PLATFORMS = ("youtube", "bilibili")

DEFAULT_CONFIG = {
    "poll_interval_mins": 60,
    "transcode": False,
    "cookies_browser": "chrome",
    "state_path": os.path.expanduser("~/.publish-video-watcher/state.json"),
    "platforms": {
        "youtube": {"source": "watch_later"},
        "bilibili": {"source": "watch_later"},
    },
    "actions": [{"name": "mytv", "enabled": False, "channel": 0}],
}


def parse_config(text: str) -> dict:
    raw = tomllib.loads(text)
    # Shallow merge: any top-level key present in the file replaces the default
    # wholesale. In particular, providing ANY [platforms.*] section replaces the
    # whole platforms table (so you can poll just one platform by listing only it),
    # and an [[actions]] array replaces the default actions list entirely.
    return {**DEFAULT_CONFIG, **raw}


def load_config(path: str) -> dict:
    with open(path) as f:
        return parse_config(f.read())


def validate_config(cfg: dict) -> None:
    """Reject configs with an unknown platform or an actions entry missing a name."""
    for plat in cfg["platforms"]:
        if plat not in KNOWN_PLATFORMS:
            raise ValueError(f"unknown platform in config: {plat}")
    for a in cfg["actions"]:
        if "name" not in a:
            raise ValueError("each [[actions]] entry needs a name")


def build_publish_cmd(url, script_path, transcode, cookies_browser) -> list:
    cmd = ["python3", script_path, url, "--cookies-from-browser", cookies_browser]
    if transcode:
        cmd.append("--transcode")
    return cmd


def run_publish(url, script_path, transcode, cookies_browser, run_fn=subprocess.run) -> dict:
    cmd = build_publish_cmd(url, script_path, transcode, cookies_browser)
    proc = run_fn(cmd, capture_output=True, text=True)
    # exit 0 = all ok, 1 = item failed (envelope still printed), 2 = config/usage error.
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"publish failed (exit {proc.returncode}): {proc.stderr.strip()[:300]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"could not parse publish output: {e}")


def first_result(envelope: dict):
    results = envelope.get("results", [])
    return results[0] if results else None


def make_result(entry: dict, published: dict) -> dict:
    return {
        "platform": entry["platform"],
        "source_id": entry["id"],
        "title": published.get("title", entry.get("title", "")),
        "public_url": published["public_url"],
        "duration_secs": published.get("duration_secs", 0),
    }
