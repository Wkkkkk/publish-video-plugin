---
name: publish-video
description: Use when you need to publish a local or remote video — a file, a direct media URL, a yt-dlp-supported site URL, or a folder of videos — to a public URL. Downloads/normalizes to a browser-playable H.264/AAC MP4, uploads to S3-compatible object storage, and returns the public URL. Optionally registers it as a MyTV VOD playlist item.
---

# publish-video

Publish one or more videos to a public URL via S3-compatible object storage.

## When to use
- You need a hosted, browser-playable MP4 URL for a video (to embed, share, or feed another system).
- Sources can be: a local file, a local folder, a direct `https://…/x.mp4` link, or any yt-dlp-supported site (YouTube, Bilibili, etc.).

## When NOT to use
- You need HLS/DASH manifest hosting, or private/signed delivery (this tool is public-read only).

## Prerequisites
- `python3` with `boto3` (`pip install boto3`)
- `yt-dlp` (only for site URLs), `ffmpeg`/`ffprobe` (ffprobe always; ffmpeg only with `--transcode`)
- Required env: `PUBLISH_VIDEO_S3_ENDPOINT`, `PUBLISH_VIDEO_S3_BUCKET`, `PUBLISH_VIDEO_PUBLIC_BASE_URL`. Bucket credentials come from boto3's standard chain (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, or `~/.aws/credentials`, etc.). For the MyTV sink only: `MYTV_BASE_URL`, `MYTV_ADMIN_PASSWORD`.

The script fails with a clear stderr message + exit code 2 if a required tool or one of the three `PUBLISH_VIDEO_*` vars is missing. Missing bucket credentials surface as a per-item upload error instead.

The config is read from the environment. The convenient way to set it is a `.env` file (see the repo's `.env.example`): `set -a; source /path/to/.env; set +a` before invoking, which exports everything for the tool.

## How to invoke
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/publish-video/scripts/publish_video.py <source> [more sources…] [options]
```

## How to read the result
The script prints a JSON envelope to **stdout** (logs/warnings go to stderr, so stdout is safe to parse):
```json
{ "ok": 1, "failed": 0, "results": [ { "public_url": "https://…/video/<id>-<name>.mp4", "duration_secs": 193, "passthrough": true, "transcoded": false } ] }
```
For each entry in `results`, read `public_url` (success) or `error` (failure). Exit code is `0` if all succeeded, `1` if any item failed (others still processed), `2` on a config/usage error.

## Examples
```bash
PV="${CLAUDE_PLUGIN_ROOT}/skills/publish-video/scripts/publish_video.py"

# yt-dlp site URL (downloads + muxes to H.264/AAC):
python3 "$PV" "https://www.bilibili.com/video/BV1xx"

# Direct media URL (downloaded as-is):
python3 "$PV" "https://cdn.example.com/clip.mp4"

# Local file with a title override, forcing a browser-safe re-encode:
python3 "$PV" ./movie.mkv --title "Movie" --transcode

# A whole folder, recursively:
python3 "$PV" ~/Videos/exports --recursive

# Batch from a list, register each into MyTV channel 7:
python3 "$PV" --from-file urls.txt --sink mytv --channel 7

# Preview without downloading/uploading/registering:
python3 "$PV" ./a.mp4 --dry-run
```

See `REFERENCE.md` for the full flag/env table and JSON schema.
