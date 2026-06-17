# publish-video — Reference

## Flags
| Flag | Default | Purpose |
|------|---------|---------|
| `SOURCE…` (positional) | — | One or more: yt-dlp URL, direct media URL, local file, local directory |
| `--from-file FILE` | — | Read additional sources, one per line (`#` comments, blanks ignored) |
| `--recursive` | off | Descend into subdirectories for directory sources |
| `--title TITLE` | derived | Title override (rejected when the run resolves to more than one video) |
| `--key-prefix PREFIX` | `video` | Object key prefix |
| `--cookies-from-browser B` | `chrome` | Browser for yt-dlp cookies (URL sources) |
| `--format-sort SORT` | `vcodec:h264,acodec:aac` | yt-dlp `-S` string |
| `--concurrent-fragments N` | `1` | yt-dlp `-N`: parallel fragment downloads per video |
| `--js-runtimes RT` | `node` | yt-dlp `--js-runtimes`: JS runtime for YouTube challenge solving (`""` disables) |
| `--remote-components RC` | `ejs:github` | yt-dlp `--remote-components`: fetch the EJS solver script (`""` disables) |
| `--transcode` | off | Re-encode non-H.264/AAC inputs to H.264/AAC (else warn + upload as-is) |
| `--sink {print,mytv}` | `print` | Output sink; `mytv` also registers a playlist item |
| `--channel N` | — | MyTV channel id (required with `--sink mytv`) |
| `--dry-run` | off | Print planned actions; no download/upload/register |

> **YouTube:** modern yt-dlp requires an enabled JavaScript runtime to solve YouTube's
> signature + n-challenge (only `deno` is enabled by default). The defaults above opt in
> to an installed runtime (`node`) and fetch yt-dlp's official EJS solver script from
> GitHub on first use (cached). Requires `node` (or `deno`/`bun`) on PATH; harmless for
> sites that run no JS challenge (e.g. Bilibili). Set both flags to `""` to disable.

## Environment
| Var | When | Purpose |
|-----|------|---------|
| `PUBLISH_VIDEO_S3_ENDPOINT` | always | S3-compatible endpoint URL |
| `PUBLISH_VIDEO_S3_BUCKET` | always | Bucket name |
| `PUBLISH_VIDEO_PUBLIC_BASE_URL` | always | Public base URL of the bucket |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | upload | boto3 credentials (or any other source in boto3's standard chain, e.g. `~/.aws/credentials`) |
| `MYTV_BASE_URL` / `MYTV_ADMIN_PASSWORD` | `--sink mytv` | MyTV API base + admin password |

## JSON output (stdout)
```json
{
  "ok": 2,
  "failed": 1,
  "results": [
    {"source": "...", "type": "ytdlp_url", "title": "...", "public_url": "https://...",
     "object_key": "video/<platform>-<YYYYMMDD>-<videoid>-<title>.mp4", "duration_secs": 193,
     "passthrough": false, "transcoded": false},
    {"source": "...", "type": "local_file", "error": "ffprobe failed: ..."}
  ]
}
```
- `object_key` — `<prefix>/<platform>-<YYYYMMDD>-<videoid>-<title>.mp4`. The title is ASCII-only — non-ASCII (CJK, etc.) is dropped so the URL needs no percent-encoding; an all-non-ASCII title reduces to a generic `video` stem. The `videoid` (parsed from the source URL) makes a same-day re-publish overwrite rather than duplicate and keeps the key unique even when the title is generic; when no id can be parsed (e.g. local files) a short random suffix is used instead.
- `passthrough` — the file was already a browser-playable MP4 and uploaded unchanged.
- `transcoded` — the file was re-encoded to H.264/AAC (`--transcode`).
- With `--sink mytv`, successful items also carry `"mytv_item": <id>`.
- With `--dry-run`, each result carries `"dry_run": true` and a planned `object_key`/`public_url`. For yt-dlp sources the predicted title is `"Untitled"` and the predicted key is therefore not authoritative (the real run uses the fetched title).

## Source classification
| Input | Type | Handling |
|-------|------|----------|
| Existing directory | `directory` | Expands to one `local_file` job per contained video (`.mp4/.webm/.mov/.m4v/.mkv/.avi`); `--recursive` descends |
| Existing file | `local_file` | Used in place (never deleted) |
| `http(s)` URL ending `.mp4/.webm/.mov/.m4v` | `direct_url` | Downloaded directly (no extractor) |
| Any other `http(s)` URL | `ytdlp_url` | Downloaded + muxed to H.264/AAC via yt-dlp |

## Exit codes
- `0` — all items succeeded
- `1` — at least one item failed (others still processed)
- `2` — config/usage error (missing env/tool, unreadable `--from-file`, bad arguments)

## Watch Later watcher (`watcher.py`)

A standalone poller that watches a saved-video source on YouTube + Bilibili and publishes new items via `publish_video.py`. Source listing uses `yt-dlp --flat-playlist` (list-only); the source itself is never modified — dedup is tracked in a local state file.

### Setup
```bash
cp skills/publish-video/scripts/watcher.example.toml watcher.toml   # then edit
```
It relies on the same environment as the engine (`PUBLISH_VIDEO_*`, plus `MYTV_*` for the `mytv` action) and on your browser cookies (Watch Later is private — set `cookies_browser`).

### CLI
| Flag | Default | Purpose |
|------|---------|---------|
| `--config FILE` | `watcher.toml` | TOML config path |
| `--once` | off | Run a single pass, then exit (use this for cron/manual runs) |
| `--platform {youtube,bilibili}` | all | Poll only one platform |
| `--dry-run` | off | List new items per platform as JSON; no publish |
| `--limit N` | config `max_items` | Cap each source to its N latest items this run |
| `--concurrency N` | config `concurrency` | How many videos to download/upload at once this run |

Loop mode (no `--once`) polls every `poll_interval_mins`.

### Config
- `max_items` — only the N latest items per source are listed each pass (default 10; `0` = no cap). Caps cost at the source via `yt-dlp --playlist-end`, including the per-item title fetch below.
- `concurrency` — how many videos download/upload at once (default 5). A bounded thread pool; each video runs in its own engine subprocess.
- `concurrent_fragments` — passed to yt-dlp as `-N` (default 4), parallelizing one video's fragment downloads. Speeds up a single large video.
- `state_path` — local dedup record (leading `~` is expanded). Never your source.
- `platforms.<name>.source` — `watch_later` or a full playlist/folder URL. Bare IDs are not supported in v1. Naming any platform replaces the default platforms table wholesale, so list every platform you want polled.
- `actions` — ordered per-video post-publish steps (run once per published video). None built in for v1. Add one via a function + an `ACTIONS` entry + an `[[actions]]` block.
- `post_run` — ordered run-level actions (run once per poll, after publishing), via a parallel `[[post_run]]` registry. Built in: `notify` (macOS Notification Center), `mytv` (auto-register published videos into MyTV), and `summarize` (analyze each published video with the external `video-summarizer` CLI). Add one via a function + a `POST_RUN_ACTIONS` entry + a `[[post_run]]` block.

### Behavior & limitations (v1)
- Read-only source; failed publishes are not recorded and retry next pass.
- A listing failure on one platform does not stop the others.
- `summarize` (a `[[post_run]]` action) analyzes the **uploaded R2 URL**, not the local file (which the engine deletes after upload), so it runs at the run level rather than per-video.

### Scheduling
Run `python3 .../watcher.py --once` from a local Claude routine (`/schedule`) or an OS cron/launchd timer. It must run where your cookies + `PUBLISH_VIDEO_*`/`MYTV_*` env resolve (i.e. locally).

The agent logs to `~/.publish-video-watcher/watcher.log`. The wrapper
(`run-watcher.example.sh` in this directory is a template) rotates that log via
copytruncate when it exceeds 5 MB, keeping one previous generation (`watcher.log.1`).
Each poll ends with a one-line summary: `run done: N published, M failed`
(plus ` · K listing errors` when a platform's listing fails).

`[[post_run]]` actions run once after each poll. `notify` (macOS Notification Center)
takes `trigger` = `activity` (published or failed > 0, or a listing error) | `failure` | `always`.
`mytv` auto-registers each published video into a MyTV channel **per platform**, creating the
channel if missing: the channel name is `channels.<platform>` (e.g. `youtube = "MyYoutube"`),
defaulting to `"My" + Platform` when unset; `type` defaults to `vod_on_demand`. Needs
`MYTV_BASE_URL` + `MYTV_ADMIN_PASSWORD` in the environment.

`summarize` runs the external `video-summarizer` CLI over each published video's R2 `public_url`,
writing `<out>/<slug>.md` (transcript + summary + chapters). Options: `command` (CLI path — `pipx`
installs it on PATH at `~/.local/bin/video-summarizer`; under launchd's minimal PATH give the
absolute path, or add `~/.local/bin` to the wrapper's PATH), `out` (output dir, default
`~/video-analyses`), `lang`
(`""` = auto-detect), `whisper_model` (`""` = the CLI's default; set e.g. `base` to match an
installed model), `summary_backend` (`""` = the CLI's default `gemini`; set `claude` for the
Anthropic backend), `summary_model` (`""` = the backend's default model; e.g. `gemini-flash-latest`
for a cheaper pass), `cwd` (working dir for the CLI — set to the video-summarizer project dir if the
CLI resolves its whisper model path relative to its working directory), `visual` (off; the
expensive Gemini Pro pass — run by hand instead), `notify` (one summary notification per run), and
`max_workers` (videos summarized concurrently, default `3`). The per-video CLI is CPU-bound on
Whisper transcription, so a small cap overlaps the Gemini wait without oversubscribing cores;
1 disables concurrency. Requires `video-summarizer` installed plus a backend API key in the
environment — `GEMINI_API_KEY` for the default backend, or `ANTHROPIC_API_KEY` with
`summary_backend = "claude"` (fold whichever into the watcher `.env`). No cookies flag is passed:
the action summarizes each video's already-public R2 URL, not the original (possibly login-gated)
source, so there is no session to authenticate. A per-video failure is logged and skipped; the run
still completes.
