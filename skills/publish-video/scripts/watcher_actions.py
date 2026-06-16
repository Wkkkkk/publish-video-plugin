"""Post-publish actions. Each action is `run(result, opts) -> dict`, where `result`
carries {platform, source_id, title, public_url, duration_secs}. The pipeline
isolates per-action failures. Registry-based: a new action is a new function + one
ACTIONS entry + a config line."""
from __future__ import annotations

import os
import subprocess
import sys

import publish_video  # reuse the engine's MyTV helpers, unchanged


def run_summarize(result, opts, **_) -> dict:
    # Stub: summarization not implemented in v1. A real version would need the local
    # file, which the shell-out engine deletes after upload — see plan "Known limits".
    return {"skipped": "summarize not implemented"}


def send_macos_notification(title, message, run_fn=subprocess.run) -> None:
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    run_fn(["osascript", "-e", script], capture_output=True, text=True)


def notify_action(run_context, opts, log=None, send_fn=send_macos_notification) -> dict:
    """Run-level notifier driven by the run summary. Returns {notified: bool, ...}."""
    if not opts.get("enabled"):
        return {"notified": False, "reason": "disabled"}
    outcomes = run_context.get("outcomes", [])
    published = sum(1 for o in outcomes if o.get("ok"))
    failed = len(outcomes) - published
    errors = len(run_context.get("listing_errors") or [])
    trigger = opts.get("trigger", "activity")
    should = (
        trigger == "always"
        or (trigger == "failure" and (failed or errors))
        or (trigger == "activity" and (published or failed or errors))
    )
    if not should:
        return {"notified": False, "reason": "trigger not met"}
    message = run_context.get("summary", "").removeprefix("run done: ")
    send_fn(opts.get("title", "publish-video watcher"), message)
    return {"notified": True}


def default_channel_name(platform: str) -> str:
    return "My" + platform.title()


def mytv_action(run_context, opts, log=None, env=None,
                list_channels=publish_video.list_channels,
                ensure_channel=publish_video.ensure_channel,
                register_item=publish_video.register_item,
                build_payload=publish_video.build_payload) -> dict:
    """Run-level: register each successful item into its platform's MyTV channel,
    creating the channel if missing. Groups by platform; one ensure per platform."""
    log = log or (lambda m: None)
    env = os.environ if env is None else env
    base = env.get("MYTV_BASE_URL")
    password = env.get("MYTV_ADMIN_PASSWORD")
    if not base or not password:
        raise RuntimeError("mytv action needs MYTV_BASE_URL and MYTV_ADMIN_PASSWORD")
    items = [o["result"] for o in run_context.get("outcomes", []) if o.get("ok")]
    if not items:
        return {"skipped": "no items"}
    by_platform = {}
    for r in items:
        by_platform.setdefault(r["platform"], []).append(r)
    channels_cfg = opts.get("channels", {})
    ctype = opts.get("type", "vod_on_demand")
    category = opts.get("category", "")
    existing = list_channels(base, password)
    registered = 0
    channel_ids = {}
    for platform, plat_items in by_platform.items():
        name = channels_cfg.get(platform) or default_channel_name(platform)
        try:
            cid = ensure_channel(base, password, name, category, ctype, existing)
        except Exception as e:
            log(f"mytv: ensure channel {name!r} failed: {e}")
            continue
        channel_ids[platform] = cid
        for r in plat_items:
            try:
                register_item(base, cid, password,
                              build_payload(r["title"], r["public_url"], r["duration_secs"]))
                registered += 1
            except Exception as e:
                log(f"mytv: register {r.get('title')!r} failed: {e}")
    return {"registered": registered, "channels": channel_ids}


def summarize_action(run_context, opts, log=None, env=None,
                     run_fn=subprocess.run, send_fn=send_macos_notification) -> dict:
    """Run-level: summarize each published video with the external `video-summarizer`
    CLI, feeding it the R2 public_url. Writes one markdown per video (the CLI prints
    its path), isolates per-item failures (a missing CLI aborts the whole action),
    and sends one summary notification per run."""
    log = log or (lambda m: None)
    env = os.environ if env is None else env
    items = [o["result"] for o in run_context.get("outcomes", []) if o.get("ok")]
    if not items:
        return {"skipped": "no items"}
    command = opts.get("command", "video-summarizer")
    out_dir = os.path.expanduser(opts.get("out", "./analyses"))
    lang = opts.get("lang") or ""
    visual = opts.get("visual", False)
    analyses = []
    for r in items:
        cmd = [command, r["public_url"], "--out", out_dir]
        if lang:
            cmd += ["--lang", lang]
        if visual:
            cmd.append("--visual")
        try:
            proc = run_fn(cmd, capture_output=True, text=True, env=env)
        except FileNotFoundError:
            raise RuntimeError(f"summarize: command not found: {command}")
        stdout = (proc.stdout or "").strip()
        path = stdout.splitlines()[-1].strip() if stdout else ""
        if path.endswith(".md"):  # CLI prints the written path; .md => a file exists
            analyses.append({"title": r["title"], "path": path})
        else:
            log(f"summarize: {r.get('title')!r} failed (exit {proc.returncode}): "
                f"{(proc.stderr or '').strip()[:200]}")
    if analyses and opts.get("notify", True):
        titles = ", ".join(a["title"] for a in analyses)
        send_fn(opts.get("title", "video-summarizer"),
                f"{len(analyses)} analyses → {out_dir}: {titles}")
    return {"summarized": len(analyses), "out": out_dir, "analyses": analyses}


ACTIONS = {
    "summarize": run_summarize,
}

POST_RUN_ACTIONS = {
    "notify": notify_action,
    "mytv": mytv_action,
    "summarize": summarize_action,
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


def run_post_run(run_context, post_run_config, registry=None, log=None) -> list:
    """Run-level actions. Each is fn(run_context, opts, log) -> dict; failures isolated."""
    registry = POST_RUN_ACTIONS if registry is None else registry
    log = log or (lambda m: print(m, file=sys.stderr))
    outcomes = []
    for name, opts in enabled_actions(post_run_config):
        fn = registry.get(name)
        if fn is None:
            outcomes.append({"action": name, "ok": False, "error": "unknown action"})
            log(f"post-run {name}: unknown, skipped")
            continue
        try:
            output = fn(run_context, opts, log=log)
            outcomes.append({"action": name, "ok": True, "output": output})
        except Exception as e:
            outcomes.append({"action": name, "ok": False, "error": str(e)})
            log(f"post-run {name} failed: {e}")
    return outcomes


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
