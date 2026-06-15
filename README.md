# publish-video

A Claude Code plugin: a skill that publishes a video (local file, direct URL, yt-dlp-supported site, or folder) to a public URL on S3-compatible object storage, returning the URL as JSON. Optionally registers the result as a MyTV VOD item.

## Install (local)
```bash
/plugin marketplace add /absolute/path/to/publish-video-plugin
/plugin install publish-video
```

## Configuration
Copy the template and fill in your storage details (gitignored — keep secrets out of git):
```bash
cp .env.example .env       # then edit .env
set -a; source .env; set +a
```
`set -a` exports every variable the `source` defines, so the tool (and the skill) pick them up. See `.env.example` for the full list.

## Prerequisites & usage
See `skills/publish-video/SKILL.md` and `skills/publish-video/REFERENCE.md`.

## Tests
```bash
cd skills/publish-video/scripts && python3 -m unittest test_publish_video -v
```
