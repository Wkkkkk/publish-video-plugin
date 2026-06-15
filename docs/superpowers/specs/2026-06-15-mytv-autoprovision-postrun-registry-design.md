# Design: Self-provisioning per-platform MyTV + run-level action registry

Date: 2026-06-15
Status: Approved (brainstorm), pending implementation plan

## Purpose

Two coupled improvements to the watcher's post-publish behavior:

1. **Self-provisioning MyTV channels, per platform.** Today the `mytv` action
   registers every video to a single, pre-created channel whose id is hardcoded
   (`channel = 38`). Make it create-if-missing and route per platform: YouTube items
   to a "MyYoutube" channel, Bilibili items to "MyBilibili" — each ensured to exist at
   run time. No manual channel pre-creation, no hardcoded id.

2. **A generic run-level action registry.** `notify` (shipped) and `mytv` are both
   *run-level* steps but are/were hand-wired into `run_once`. Generalize them into a
   `[[post_run]]` registry symmetric to the existing per-video `[[actions]]` registry,
   so future run-level actions (webhook, digest, RSS, …) are "add a function + a config
   block." Fold both `notify` and `mytv` into it.

## Decisions (from brainstorm)

- **Topology:** one channel per platform.
- **Channel names:** mirror the source folders — `youtube → "MyYoutube"`,
  `bilibili → "MyBilibili"`. Configurable; default **derived** as `"My" + platform.title()`
  so a new platform is zero-config.
- **Channel type:** `vod_on_demand` (matches the existing "Saved Videos" channel).
- **Generalize run-level actions now**, folding in `notify`.
- **Channel 38** ("Saved Videos") is left as-is; new items route to the per-platform
  channels. Migrating its items is out of scope (optional later).

## Components / changes

### 1. Engine MyTV helpers (`publish_video.py`)

The MyTV API code already lives here (`build_register_url`, `register_item`,
`build_payload`). Add three thin, injectable helpers. API shapes confirmed against the
MyTV server (`src/routes/api/channels.rs`):

- `list_channels(base, password, fetch_fn=...) -> list[dict]` — GET `/api/admin/channels`
  (basic auth), returns `[{id, name, type, ...}]`.
- `create_channel(base, password, name, category, channel_type, send_fn=...) -> dict` —
  POST `/api/admin/channels` (basic auth) with body
  `{"name": name, "category": category, "type": channel_type, "sort_order": 0}`
  (`logo_url`/`loop_anchor` omitted; server treats them as optional). Returns the created
  channel `{id, ...}` (HTTP 201).
- `ensure_channel(base, password, name, category, channel_type, existing) -> int` — find a
  channel whose `name` matches (exact) in `existing`; if found return its `id`, else
  `create_channel(...)` and return the new id. `existing` is the already-fetched
  `list_channels` result (so the caller fetches once per run).

Reuse the existing basic-auth header construction from `register_item`. These use
`urllib` like the rest of the engine's MyTV code. The engine's `--sink mytv --channel N`
CLI is **unchanged** (explicit id); auto-create is the watcher's concern.

### 2. Run-level action registry (`watcher_actions.py`)

A run-level action is `fn(run_context, opts, log=...) -> dict`, where:

```python
run_context = {
    "outcomes": [...],         # tick's per-item outcomes; successful ones carry result{}
    "listing_errors": [...],   # platforms whose listing raised
    "summary": "run done: N published, M failed[ · K listing errors]",
}
```

- `POST_RUN_ACTIONS = {"notify": notify_action, "mytv": mytv_action}` — distinct from the
  per-video `ACTIONS` registry.
- `run_post_run(run_context, post_run_config, registry=POST_RUN_ACTIONS, log=...) -> list`
  — iterates `enabled_actions(post_run_config)` (the existing filter, reused), dispatches
  each `fn(run_context, opts, log=log)`, isolates per-action failures into
  `{"action": name, "ok": bool, ...}` (mirrors `run_actions`). Unknown action name →
  `{ok: False, error: "unknown action"}`.

### 3. `notify` folded in (`watcher_actions.py`)

Replace `notify_run(result, notify_cfg, message, ...)` with
`notify_action(run_context, opts, send_fn=send_macos_notification) -> dict`:
- computes `published`/`failed` from `run_context["outcomes"]`, `errors` from
  `listing_errors`; same trigger logic (`activity`/`failure`/`always`, default `activity`).
- message = `run_context["summary"].removeprefix("run done: ")`.
- `send_macos_notification` is unchanged.

### 4. `mytv_action` (`watcher_actions.py`)

`mytv_action(run_context, opts, env=None, deps=None) -> dict` where `deps` injects the
engine helpers (`list_channels`, `ensure_channel`, `register_item`, `build_payload`) for
testing; defaults to the real `publish_video` functions.

- `base`/`password` from `env` (`MYTV_BASE_URL`/`MYTV_ADMIN_PASSWORD`); raise if missing.
- Collect successful items: `[o["result"] for o in run_context["outcomes"] if o.get("ok")]`
  (each `result` has `platform`, `title`, `public_url`, `duration_secs`).
- If no items, return `{"skipped": "no items"}`.
- `existing = list_channels(base, password)` (fetched once).
- Group items by `result["platform"]`. For each platform:
  - `name = opts.get("channels", {}).get(platform) or default_channel_name(platform)`.
  - `cid = ensure_channel(base, password, name, opts.get("category",""),
    opts.get("type","vod_on_demand"), existing)` — on failure, log and skip this platform.
  - For each item: `register_item(base, cid, password, build_payload(title, public_url,
    duration_secs))`; on failure log and continue.
- Return `{"registered": <count>, "channels": {platform: cid, ...}}`.

`default_channel_name(platform) = "My" + platform.title()` (a small pure helper;
`"youtube" → "MyYoutube"`, `"bilibili" → "MyBilibili"`).

Runs serially (post-`tick`, outside the concurrent pool), so channel-ensure is race-free
with no lock.

### 5. Config (`watcher.py`, `watcher.toml`, `watcher.example.toml`)

`DEFAULT_CONFIG`:
- Remove the top-level `notify` block and drop `mytv` from `actions`.
- Add `"post_run"`:
  ```python
  "post_run": [
      {"name": "notify", "enabled": False, "trigger": "activity", "title": "publish-video watcher"},
      {"name": "mytv", "enabled": False, "type": "vod_on_demand", "category": "saved",
       "channels": {"youtube": "MyYoutube", "bilibili": "MyBilibili"}},
  ],
  ```
- `actions` default becomes `[{"name": "summarize", "enabled": False}]` (per-video stub kept).

`build_deps()`: replace `"notify": notify_run` with `"run_post_run": watcher_actions.run_post_run`.

`run_once`:
```python
result = tick(cfg, script_path, deps, log)
summary = format_summary(result)
log(summary)
run_context = {"outcomes": result["outcomes"],
               "listing_errors": result["listing_errors"], "summary": summary}
try:
    deps["run_post_run"](run_context, cfg["post_run"], log=log)
except Exception as e:
    log(f"post-run actions failed: {e}")
return result
```

TOML (both `watcher.toml` and `watcher.example.toml`): replace the `[notify]` block and the
`[[actions]] mytv` block with `[[post_run]]` entries (notify + mytv), using an **inline
table** for `channels` (TOML array-of-tables can't take a `[post_run.channels]` subtable
cleanly):
```toml
[[post_run]]
name = "mytv"
enabled = true
type = "vod_on_demand"
category = "saved"
channels = { youtube = "MyYoutube", bilibili = "MyBilibili" }
```

## Data flow

`tick` → `{outcomes, listing_errors}` → `run_once` builds `run_context` (+ summary) →
`run_post_run` → for each enabled post-run action: `notify_action` (desktop alert) and
`mytv_action` (group successful items by platform → `ensure_channel` once per platform →
`register_item` each).

## Error handling

- Every post-run action is isolated by `run_post_run`; one failing action does not stop
  the others, and none can abort the run (publishing already happened in `tick`).
- Within `mytv_action`: a channel-ensure failure skips only that platform; a single
  `register_item` failure skips only that item. All logged.
- Missing `MYTV_*` env → `mytv_action` raises, caught by `run_post_run`, logged.

## Testing

- `default_channel_name`: `youtube`/`bilibili`/a novel platform.
- `list_channels`/`create_channel`/`ensure_channel`: found-existing (no create) vs
  not-found (creates), via injected `fetch_fn`/`send_fn` — no network.
- `run_post_run`: dispatches enabled actions, isolates a raising action, unknown-name handling.
- `notify_action`: trigger matrix (activity/failure/always × published/failed/listing-error/
  idle/disabled) reading `run_context`, with a fake `send_fn`.
- `mytv_action`: groups by platform; ensures each platform's channel exactly once;
  registers each item; uses configured name and falls back to the derived default; isolates
  a per-platform ensure failure and a per-item register failure — all with injected fake
  engine helpers (record calls, no network).
- `run_once`: builds `run_context` and calls `run_post_run` (DI, no network).

All deterministic via dependency injection.

## Migration notes

- This re-homes the just-shipped `notify` (top-level `[notify]` → `[[post_run]]`). Its
  unit tests move from `notify_run` to `notify_action`/`run_post_run` shapes.
- Retires the per-video `run_mytv` action and the fixed `channel = 38` config; `mytv` is
  now a run-level action. The per-video `[[actions]]` registry remains (currently just the
  `summarize` stub).
- The live `watcher.toml` and the launchd setup are updated to the `[[post_run]]` shape;
  channel 38 and its items are left untouched.
- REFERENCE.md: document the `[[post_run]]` registry, the `mytv` action (auto-create,
  per-platform names, derived default), and that `notify` is now a post-run action.

## Out of scope (v1)

- Migrating channel 38's existing items to the per-platform channels.
- Per-platform channel category/type overrides (single `type`/`category` for all).
- Auto-create in the engine's standalone `--sink mytv` CLI (stays explicit-id).
- Non-`name` channel matching (e.g. by a stored tag) — match is by exact name.
