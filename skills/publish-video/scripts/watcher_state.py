"""Dedup state for the watcher: which {platform}:{id} items were already handled.
The saved-video source is never modified; this file is the only record of progress."""
from __future__ import annotations

import json
import os


def entry_key(entry: dict) -> str:
    return f"{entry['platform']}:{entry['id']}"


def load_state(path: str) -> set:
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return set(json.load(f))


def save_state(path: str, keys) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(sorted(keys), f, indent=2)


def new_entries(entries, seen) -> list:
    return [e for e in entries if entry_key(e) not in seen]


# --- MyTV pending-registration queue ---------------------------------------
# A video is published and recorded in state.json *before* the MyTV post-run
# action runs, so a MyTV outage would otherwise lose the registration forever
# (the video is never re-listed). This queue holds result dicts that published
# but failed to register; the mytv action retries them on every later pass and
# clears them once registered. It lives beside state.json.

def pending_path_for(state_path: str) -> str:
    return os.path.join(os.path.dirname(state_path) or ".", "mytv_pending.json")


def load_pending(path) -> list:
    if not path or not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def save_pending(path, items) -> None:
    if not path:  # pending disabled (no state_path available) -> no-op
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(items, f, indent=2)
