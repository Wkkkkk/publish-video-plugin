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
| `--transcode` | off | Re-encode non-H.264/AAC inputs to H.264/AAC (else warn + upload as-is) |
| `--sink {print,mytv}` | `print` | Output sink; `mytv` also registers a playlist item |
| `--channel N` | — | MyTV channel id (required with `--sink mytv`) |
| `--dry-run` | off | Print planned actions; no download/upload/register |

## Environment
| Var | When | Purpose |
|-----|------|---------|
| `PUBLISH_VIDEO_S3_ENDPOINT` | always | S3-compatible endpoint URL |
| `PUBLISH_VIDEO_S3_BUCKET` | always | Bucket name |
| `PUBLISH_VIDEO_PUBLIC_BASE_URL` | always | Public base URL of the bucket |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | always | boto3 credentials |
| `MYTV_BASE_URL` / `MYTV_ADMIN_PASSWORD` | `--sink mytv` | MyTV API base + admin password |

## JSON output (stdout)
```json
{
  "ok": 2,
  "failed": 1,
  "results": [
    {"source": "...", "type": "ytdlp_url", "title": "...", "public_url": "https://...",
     "object_key": "video/<id>-<name>.mp4", "duration_secs": 193,
     "passthrough": false, "transcoded": false},
    {"source": "...", "type": "local_file", "error": "ffprobe failed: ..."}
  ]
}
```
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
