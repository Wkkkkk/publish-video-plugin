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
import threading
from concurrent.futures import ThreadPoolExecutor
import tomllib

import watcher_actions
import watcher_sources
import watcher_state

KNOWN_PLATFORMS = ("youtube", "bilibili")

DEFAULT_CONFIG = {
    "poll_interval_mins": 60,
    "transcode": False,
    "max_items": 10,  # cap each source to its N latest items per pass (0 = no cap)
    "concurrency": 5,  # how many videos to download/upload at once
    "concurrent_fragments": 4,  # yt-dlp -N: parallel fragment downloads per video
    "cookies_browser": "chrome",
    "state_path": os.path.expanduser("~/.publish-video-watcher/state.json"),
    "platforms": {
        "youtube": {"source": "watch_later"},
        "bilibili": {"source": "watch_later"},
    },
    "actions": [{"name": "mytv", "enabled": False, "channel": 0}],
    "notify": {"enabled": False, "trigger": "activity", "title": "publish-video watcher"},
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
    cfg["state_path"] = os.path.expanduser(cfg["state_path"])


def build_publish_cmd(url, script_path, transcode, cookies_browser, concurrent_fragments=1) -> list:
    cmd = ["python3", script_path, url, "--cookies-from-browser", cookies_browser]
    if transcode:
        cmd.append("--transcode")
    if concurrent_fragments and concurrent_fragments > 1:
        cmd += ["--concurrent-fragments", str(concurrent_fragments)]
    return cmd


def run_publish(url, script_path, transcode, cookies_browser, concurrent_fragments=1,
                run_fn=subprocess.run) -> dict:
    cmd = build_publish_cmd(url, script_path, transcode, cookies_browser, concurrent_fragments)
    proc = run_fn(cmd, capture_output=True, text=True)
    if proc.stderr:  # surface the engine's own logs/errors (yt-dlp output, failures)
        print(proc.stderr, file=sys.stderr, end="")
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


def process_entry(entry, cfg, script_path, deps, log) -> dict:
    envelope = deps["publish"](entry["url"], script_path, cfg["transcode"], cfg["cookies_browser"],
                               concurrent_fragments=cfg["concurrent_fragments"])
    published = first_result(envelope)
    if not published or "error" in published:
        msg = published.get("error", "no result") if published else "no result"
        log(f"publish failed for {entry['url']}: {msg}")
        return {"entry": entry, "ok": False, "error": msg}
    result = make_result(entry, published)
    log(f"published {result['platform']}:{result['source_id']} "
        f"\"{result['title']}\" -> {result['public_url']}")
    outcomes = deps["run_actions"](result, cfg["actions"])
    return {"entry": entry, "ok": True, "result": result, "actions": outcomes}


def format_summary(result: dict) -> str:
    outcomes = result.get("outcomes", [])
    published = sum(1 for o in outcomes if o.get("ok"))
    failed = len(outcomes) - published
    line = f"run done: {published} published, {failed} failed"
    n = len(result.get("listing_errors") or [])
    if n:
        line += f" · {n} listing error" + ("s" if n != 1 else "")
    return line


def tick(cfg, script_path, deps, log) -> dict:
    seen = deps["load_state"](cfg["state_path"])
    # Listing phase (serial, cheap): snapshot all fresh entries against `seen` before
    # the pool starts, so the dedup decision is race-free.
    fresh_all = []
    listing_errors = []
    for platform, pconf in cfg["platforms"].items():
        try:
            entries = deps["list_entries"](platform, pconf["source"], cfg["cookies_browser"],
                                           max_items=cfg["max_items"])
        except Exception as e:  # one platform's listing failing must not stop the others
            log(f"listing {platform} failed: {e}")
            listing_errors.append(platform)
            continue
        fresh = deps["new_entries"](entries, seen)
        log(f"{platform}: {len(entries)} listed, {len(fresh)} new")
        fresh_all.extend(fresh)

    lock = threading.Lock()

    def work(entry):
        try:
            outcome = process_entry(entry, cfg, script_path, deps, log)
        except Exception as e:  # contain per item; other workers keep going
            log(f"error processing {entry.get('url')}: {e}")
            return {"entry": entry, "ok": False, "error": str(e)}
        if outcome["ok"]:
            with lock:  # serialize state mutation + write across workers (crash-safe)
                seen.add(deps["entry_key"](entry))
                deps["save_state"](cfg["state_path"], seen)
        return outcome

    workers = max(1, cfg["concurrency"])
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(work, fresh_all))
    # outcomes: list[{entry, ok, ...}] per fresh item; listing_errors: list[str] of platforms whose listing raised
    return {"outcomes": outcomes, "listing_errors": listing_errors}


def run_once(cfg, script_path, deps, log) -> dict:
    result = tick(cfg, script_path, deps, log)
    summary = format_summary(result)
    log(summary)
    try:  # a notifier failure must never abort the run
        deps["notify"](result, cfg["notify"], summary.removeprefix("run done: "))
    except Exception as e:
        log(f"notify failed: {e}")
    return result


ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "publish_video.py")


def build_deps() -> dict:
    return {
        "list_entries": watcher_sources.list_entries,
        "publish": run_publish,
        "run_actions": watcher_actions.run_actions,
        "load_state": watcher_state.load_state,
        "save_state": watcher_state.save_state,
        "new_entries": watcher_state.new_entries,
        "entry_key": watcher_state.entry_key,
        "notify": watcher_actions.notify_run,
    }


def select_platforms(cfg, platform):
    """Narrow cfg['platforms'] to a single requested platform, or return all of them
    when no platform is requested. Raises ValueError if the requested platform is not
    present in the config (argparse only checks it against the global platform list)."""
    if platform is None:
        return cfg["platforms"]
    if platform not in cfg["platforms"]:
        raise ValueError(f"--platform {platform} is not configured in this config file")
    return {platform: cfg["platforms"][platform]}


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Poll saved-video sources and publish new items via publish_video.py."
    )
    p.add_argument("--config", default="watcher.toml", help="path to TOML config (default: watcher.toml)")
    p.add_argument("--once", action="store_true", help="run a single pass, then exit")
    p.add_argument("--platform", choices=KNOWN_PLATFORMS, help="only poll this platform")
    p.add_argument("--dry-run", action="store_true", help="list new items per platform; do not publish")
    p.add_argument("--limit", type=int, help="cap each source to its N latest items (overrides config max_items)")
    p.add_argument("--concurrency", type=int, help="how many videos to publish at once (overrides config)")
    return p.parse_args(argv)


def main():
    args = parse_args()
    log = lambda m: print(m, file=sys.stderr)
    try:
        cfg = load_config(args.config)
        validate_config(cfg)
        cfg["platforms"] = select_platforms(cfg, args.platform)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    if args.limit is not None:
        cfg["max_items"] = args.limit
    if args.concurrency is not None:
        cfg["concurrency"] = args.concurrency
    if shutil.which("yt-dlp") is None:
        print("error: yt-dlp not found on PATH (needed to list sources)", file=sys.stderr)
        sys.exit(2)

    if args.dry_run:
        seen = watcher_state.load_state(cfg["state_path"])
        for platform, pconf in cfg["platforms"].items():
            try:
                entries = watcher_sources.list_entries(platform, pconf["source"], cfg["cookies_browser"],
                                                        max_items=cfg["max_items"])
            except Exception as e:  # match tick(): one platform failing must not kill the rest
                log(f"listing {platform} failed: {e}")
                continue
            fresh = watcher_state.new_entries(entries, seen)
            print(json.dumps({"platform": platform, "new": fresh}, indent=2))
        return

    deps = build_deps()
    if args.once:
        run_once(cfg, ENGINE, deps, log)
        return
    while True:
        run_once(cfg, ENGINE, deps, log)
        log(f"sleeping {cfg['poll_interval_mins']}m")
        time.sleep(cfg["poll_interval_mins"] * 60)


if __name__ == "__main__":
    main()
