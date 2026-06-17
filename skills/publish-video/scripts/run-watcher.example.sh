#!/bin/bash
# Template launchd wrapper for the publish-video watcher. Copy to
# ~/.publish-video-watcher/run-watcher.sh, edit REPO, then load the LaunchAgent.
# launchd gives a minimal PATH and no shell profile, so set everything explicitly.
set -euo pipefail

REPO="/absolute/path/to/publish-video-plugin"   # <-- edit this
SCRIPTS="$REPO/skills/publish-video/scripts"

# Rotate the log (copytruncate) before writing so it never grows unbounded.
# Truncating in place keeps launchd's already-open stdout/stderr fd valid.
LOG="$HOME/.publish-video-watcher/watcher.log"
MAX_BYTES=$((5 * 1024 * 1024))
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt "$MAX_BYTES" ]; then
    cp "$LOG" "$LOG.1" 2>/dev/null && : > "$LOG" || true
fi

# Homebrew bin first so yt-dlp / python3 / ffmpeg / ffprobe resolve under launchd.
# ~/.local/bin holds pipx shims (e.g. video-summarizer) — needed if the summarize
# action's `command` is the bare name rather than an absolute path.
export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# S3 / MyTV credentials + config (PUBLISH_VIDEO_*, AWS_*, MYTV_*).
set -a
# shellcheck disable=SC1090
source "$REPO/.env"
set +a

cd "$SCRIPTS"
echo "===== watcher run $(date '+%Y-%m-%d %H:%M:%S') ====="
exec python3 watcher.py --once --config "$SCRIPTS/watcher.toml"
