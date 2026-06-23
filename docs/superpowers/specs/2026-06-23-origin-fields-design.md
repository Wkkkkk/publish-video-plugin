# Design: origin + origin_url fields

**Date:** 2026-06-23  
**Status:** approved

## Goal

When the watcher pulls a video from Bilibili or YouTube, attach the originating platform and source URL to:
1. The watcher's JSON result (so consumers and logs have a traceable link back to the source video).
2. The video-summarizer markdown frontmatter (so analyses are self-contained references).

## Scope

Three files, no new modules, no schema changes to state.json or any config.

## Changes

### 1. `publish_video.py` — `build_result`

Add `"platform": detect_platform(source)` to the result dict returned by `build_result`.

`detect_platform()` already exists in the same file and is tested. This makes direct `publish-video` CLI calls also emit a `platform` field (e.g. `"bilibili"`, `"youtube"`, `"local"`) alongside the existing `source` URL.

Before:
```json
{ "source": "https://bilibili.com/video/BVxxx", "type": "ytdlp_url", "title": "...", "public_url": "...", ... }
```

After:
```json
{ "source": "https://bilibili.com/video/BVxxx", "type": "ytdlp_url", "platform": "bilibili", "title": "...", "public_url": "...", ... }
```

### 2. `watcher.py` — `make_result`

Add `"origin_url": entry["url"]` to the dict returned by `make_result`.

`entry["url"]` is the video page URL already present in the entry from `watcher_sources.py`; it was simply not forwarded.

Before:
```json
{ "platform": "bilibili", "source_id": "BV1xxx", "title": "...", "public_url": "...", "duration_secs": 1619 }
```

After:
```json
{ "platform": "bilibili", "source_id": "BV1xxx", "origin_url": "https://www.bilibili.com/video/BV1xxx", "title": "...", "public_url": "...", "duration_secs": 1619 }
```

### 3. `watcher_actions.py` — `summarize_action` / `summarize_one`

After `summarize_one` confirms the `.md` path exists, inject `origin` and `origin_url` into the file's YAML frontmatter before returning the analysis dict.

Resulting frontmatter:
```yaml
---
title: My Video
source: https://r2.dev/video/bilibili-2026-BV1xxx.mp4
duration: '10:00'
date: '2026-06-23'
transcript_source: whisper:base
origin: bilibili
origin_url: https://www.bilibili.com/video/BV1xxx
---
```

**Injection logic** (pure string manipulation, no new dependencies):
1. Read the file.
2. Locate the closing `---` of the frontmatter (second `---` occurrence at line start).
3. Insert `origin: <platform>\norigin_url: <origin_url>\n` immediately before it.
4. Write the file back.

**Edge cases — all handled silently (log + continue, never fail the summarize step):**
- No frontmatter block found → skip injection, log warning.
- `origin:` already in frontmatter → skip injection (idempotent; guards against future video-summarizer versions).
- `origin_url` absent on result (e.g. direct-publish item) → skip injection.
- File write failure → log and continue; frontmatter injection failure must not fail summarization.

## Testing

**`test_publish_video.py`**
- Assert `build_result(...)` includes `"platform"` field for a ytdlp_url source.

**`test_watcher.py`**
- Update existing `make_result` test: assert `"origin_url"` equals `entry["url"]`.
- Add `summarize_action` frontmatter injection tests:
  - Happy path: fields injected correctly into frontmatter.
  - No frontmatter: no crash, returns analysis dict unchanged.
  - `origin_url` missing on result: injection skipped, no crash.

No new test files required.

## Non-goals

- Modifying the `video-summarizer` CLI itself.
- Adding `origin_url` to `state.json` (the runtime result is sufficient).
- Changing config schema or TOML options.
