"""Post-publish actions. Each action is `run(result, opts) -> dict`, where `result`
carries {platform, source_id, title, public_url, duration_secs}. The pipeline
isolates per-action failures. Registry-based: a new action is a new function + one
ACTIONS entry + a config line."""
from __future__ import annotations

import os
import subprocess
import sys

import publish_video  # reuse the engine's MyTV helpers, unchanged


def run_mytv(result, opts, register_fn=publish_video.register_item, env=None) -> dict:
    env = os.environ if env is None else env
    base = env.get("MYTV_BASE_URL")
    password = env.get("MYTV_ADMIN_PASSWORD")
    if not base or not password:
        raise RuntimeError("mytv action needs MYTV_BASE_URL and MYTV_ADMIN_PASSWORD")
    channel = opts.get("channel")
    if channel is None:
        raise RuntimeError("mytv action needs a 'channel' in its config")
    payload = publish_video.build_payload(
        result["title"], result["public_url"], result["duration_secs"]
    )
    item = register_fn(base, channel, password, payload)
    item_id = item.get("id", item) if isinstance(item, dict) else item
    return {"mytv_item": item_id}


def run_summarize(result, opts, **_) -> dict:
    # Stub: summarization not implemented in v1. A real version would need the local
    # file, which the shell-out engine deletes after upload — see plan "Known limits".
    return {"skipped": "summarize not implemented"}


def send_macos_notification(title, message, run_fn=subprocess.run) -> None:
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    run_fn(["osascript", "-e", script], capture_output=True, text=True)


def notify_run(result, notify_cfg, message, send_fn=send_macos_notification) -> dict:
    """Run-level notifier driven by the run summary. Returns {notified: bool, ...}."""
    if not notify_cfg.get("enabled"):
        return {"notified": False, "reason": "disabled"}
    outcomes = result.get("outcomes", [])
    published = sum(1 for o in outcomes if o.get("ok"))
    failed = len(outcomes) - published
    errors = len(result.get("listing_errors") or [])
    trigger = notify_cfg.get("trigger", "activity")
    should = (
        trigger == "always"
        or (trigger == "failure" and (failed or errors))
        or (trigger == "activity" and (published or failed or errors))
    )
    if not should:
        return {"notified": False, "reason": "trigger not met"}
    send_fn(notify_cfg.get("title", "publish-video watcher"), message)
    return {"notified": True}


ACTIONS = {
    "mytv": run_mytv,
    "summarize": run_summarize,
}


def enabled_actions(actions_config) -> list:
    """actions_config: ordered list of dicts like {'name': 'mytv', 'enabled': True, 'channel': 7}.
    Returns ordered [(name, opts)] for enabled entries, opts stripped of name/enabled."""
    out = []
    for a in actions_config:
        if a.get("enabled"):
            opts = {k: val for k, val in a.items() if k not in ("name", "enabled")}
            out.append((a["name"], opts))
    return out


def run_actions(result, actions_config, registry=ACTIONS, log_fn=None) -> list:
    log = log_fn or (lambda m: print(m, file=sys.stderr))
    outcomes = []
    for name, opts in enabled_actions(actions_config):
        fn = registry.get(name)
        if fn is None:
            outcomes.append({"action": name, "ok": False, "error": "unknown action"})
            log(f"action {name}: unknown, skipped")
            continue
        try:
            output = fn(result, opts)
            outcomes.append({"action": name, "ok": True, "output": output})
        except Exception as e:  # isolate per-action failure; other actions still run
            outcomes.append({"action": name, "ok": False, "error": str(e)})
            log(f"action {name} failed: {e}")
    return outcomes
