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
