"""Post-publish actions. Each action is `run(result, opts) -> dict`, where `result`
carries {platform, source_id, title, public_url, duration_secs}. The pipeline
isolates per-action failures. Registry-based: a new action is a new function + one
ACTIONS entry + a config line."""
from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import publish_video  # reuse the engine's MyTV helpers, unchanged
import watcher_state


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


def mytv_action(run_context, opts, log=None, env=None, pending_path=None,
                list_channels=publish_video.list_channels,
                ensure_channel=publish_video.ensure_channel,
                register_item=publish_video.register_item,
                build_payload=publish_video.build_payload) -> dict:
    """Run-level: register each successful item into its platform's MyTV channel,
    creating the channel if missing. Groups by platform; one ensure per platform.

    Items published this run are merged with any left in the pending queue (videos
    that published but failed to register on an earlier pass — e.g. MyTV was
    offline). Anything that still fails to register is written back to the queue
    and retried next pass; successes are dropped from it. Without a pending path
    (state_path absent) the queue is disabled and behaviour is best-effort only."""
    log = log or (lambda m: None)
    env = os.environ if env is None else env
    base = env.get("MYTV_BASE_URL")
    password = env.get("MYTV_ADMIN_PASSWORD")
    if not base or not password:
        raise RuntimeError("mytv action needs MYTV_BASE_URL and MYTV_ADMIN_PASSWORD")
    if pending_path is None:
        state_path = run_context.get("state_path")
        pending_path = watcher_state.pending_path_for(state_path) if state_path else None
    pending = watcher_state.load_pending(pending_path)
    fresh = [o["result"] for o in run_context.get("outcomes", []) if o.get("ok")]
    # Merge queued + fresh, deduped by public_url (the registration's identity);
    # a fresh item supersedes a queued one for the same URL.
    by_url = {r["public_url"]: r for r in pending + fresh}
    items = list(by_url.values())
    if not items:
        return {"skipped": "no items"}
    by_platform = {}
    for r in items:
        by_platform.setdefault(r["platform"], []).append(r)
    channels_cfg = opts.get("channels", {})
    ctype = opts.get("type", "vod_on_demand")
    category = opts.get("category", "")
    try:
        existing = list_channels(base, password)
    except Exception as e:  # MyTV unreachable: queue everything, lose nothing
        log(f"mytv: list channels failed, queued {len(items)} item(s) for retry: {e}")
        watcher_state.save_pending(pending_path, items)
        return {"registered": 0, "pending": len(items), "channels": {}}
    registered = 0
    channel_ids = {}
    still_pending = []
    for platform, plat_items in by_platform.items():
        name = channels_cfg.get(platform) or default_channel_name(platform)
        try:
            cid = ensure_channel(base, password, name, category, ctype, existing)
        except Exception as e:
            log(f"mytv: ensure channel {name!r} failed, queued {len(plat_items)} item(s): {e}")
            still_pending.extend(plat_items)
            continue
        channel_ids[platform] = cid
        for r in plat_items:
            try:
                register_item(base, cid, password,
                              build_payload(r["title"], r["public_url"], r["duration_secs"]))
                registered += 1
            except Exception as e:
                log(f"mytv: register {r.get('title')!r} failed, queued for retry: {e}")
                still_pending.append(r)
    watcher_state.save_pending(pending_path, still_pending)
    out = {"registered": registered, "channels": channel_ids}
    if still_pending:
        out["pending"] = len(still_pending)
    return out


def _inject_frontmatter(path, platform, origin_url, open_fn=open, log=None):
    """Inject origin + origin_url into a markdown file's YAML frontmatter.
    Safe: silently no-ops if frontmatter is absent, fields already present,
    origin_url is empty, or any I/O error occurs."""
    log = log or (lambda m: None)
    try:
        with open_fn(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        log(f"summarize frontmatter: could not read {path}: {e}")
        return
    if not content.startswith("---\n"):
        log(f"summarize frontmatter: no frontmatter block in {path}")
        return
    if "\norigin:" in content or "\norigin_url:" in content:
        return
    close = content.find("\n---\n", 3)
    if close == -1:
        log(f"summarize frontmatter: no closing --- in {path}")
        return
    injection = f"origin: {platform}\norigin_url: {origin_url}\n"
    new_content = content[:close + 1] + injection + content[close + 1:]
    try:
        with open_fn(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except OSError as e:
        log(f"summarize frontmatter: could not write {path}: {e}")


def summarize_action(run_context, opts, log=None, env=None,
                     run_fn=subprocess.run, send_fn=send_macos_notification,
                     inject_fn=_inject_frontmatter) -> dict:
    """Run-level: summarize each published video with the external `video-summarizer`
    CLI, feeding it the R2 public_url. Writes one markdown per video (the CLI prints
    its path), isolates per-item failures (a missing CLI aborts the whole action),
    and sends one summary notification per run. Videos are summarized concurrently,
    up to `max_workers` at a time (default 3) — the per-video CLI is CPU-bound on
    Whisper transcription, so a small cap overlaps the Gemini wait without
    oversubscribing cores."""
    log = log or (lambda m: None)
    env = os.environ if env is None else env
    items = [o["result"] for o in run_context.get("outcomes", []) if o.get("ok")]
    if not items:
        return {"skipped": "no items"}
    command = opts.get("command", "video-summarizer")
    out_dir = os.path.expanduser(opts.get("out", "~/video-analyses"))
    lang = opts.get("lang") or ""
    visual = opts.get("visual", False)
    whisper_model = opts.get("whisper_model") or ""
    # Backend selection (CLI defaults to gemini when omitted). Leave both unset to
    # keep the GEMINI_API_KEY path; set summary_backend="claude" to use Claude
    # (needs ANTHROPIC_API_KEY in the watcher's environment).
    summary_backend = opts.get("summary_backend") or ""
    summary_model = opts.get("summary_model") or ""
    # The CLI resolves its model path relative to its working dir; run it from
    # the project dir via `cwd` until the tool resolves models by install location.
    cwd = os.path.expanduser(opts["cwd"]) if opts.get("cwd") else None
    max_workers = max(1, int(opts.get("max_workers", 3)))

    def summarize_one(r):
        """Summarize a single video. Returns an analysis dict on success, None on a
        per-item failure (logged). Raises RuntimeError if the CLI is missing."""
        cmd = [command, r["public_url"], "--out", out_dir, "--title", r["title"]]
        if lang:
            cmd += ["--lang", lang]
        if whisper_model:
            cmd += ["--whisper-model", whisper_model]
        if summary_backend:
            cmd += ["--summary-backend", summary_backend]
        if summary_model:
            cmd += ["--summary-model", summary_model]
        if visual:
            cmd.append("--visual")
        try:
            proc = run_fn(cmd, capture_output=True, text=True, env=env, cwd=cwd)
        except FileNotFoundError:
            raise RuntimeError(f"summarize: command not found: {command}")
        stdout = (proc.stdout or "").strip()
        path = stdout.splitlines()[-1].strip() if stdout else ""
        if path.endswith(".md"):  # CLI prints the written path; .md => a file exists
            origin_url = r.get("origin_url", "")
            platform = r.get("platform", "")
            if origin_url and platform:
                inject_fn(path, platform, origin_url, log=log)
            return {"title": r["title"], "path": path}
        log(f"summarize: {r.get('title')!r} failed (exit {proc.returncode}): "
            f"{(proc.stderr or '').strip()[:200]}")
        return None

    workers = min(max_workers, len(items))
    if workers <= 1:  # common hourly case (0-1 new videos): stay thread-free
        results = [summarize_one(r) for r in items]
    else:
        # pool.map preserves input order, so analyses follow outcome order; a
        # RuntimeError (missing CLI) surfaces when the result iterator is consumed.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(summarize_one, items))
    analyses = [a for a in results if a]
    if analyses and opts.get("notify", True):
        titles = ", ".join(a["title"] for a in analyses)
        send_fn(opts.get("title", "video-summarizer"),
                f"{len(analyses)} analyses → {out_dir}: {titles}")
    return {"summarized": len(analyses), "out": out_dir, "analyses": analyses}


ACTIONS = {}  # no per-video actions in v1; run-level actions live in POST_RUN_ACTIONS

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
