# instareader

Self-hosted reader for Instagram reels and other short videos. Send it a link and get back the caption, the full transcript, any on-screen text, and a structured summary. It works with Instagram reels and posts (including image carousels), TikTok, YouTube Shorts, X, and Facebook reels/videos.

You can use it three ways:

- **Web page**: open `https://reels.srv1172913.hstgr.cloud` (or whatever `READER_HOST` is set to) and paste a link. Processing starts on its own and the summary shows on the page, along with a list of posts you've already read. The first visit asks for an access code, which is your `HTTP_TOKEN`. The browser remembers it.
- **Telegram**: send the bot a link. It replies with the summary and attaches `transcript.txt`.
- **HTTP**: `POST /read {"url": "..."}` with a bearer token. It returns JSON.

The page is served over HTTPS by the Traefik instance already running on the VPS (the one in front of n8n), using the labels in `docker-compose.yml`. Port 8080 is bound to localhost only, for `curl` on the VPS itself.

## How it works

```
URL ─▶ yt-dlp (fallback: gallery-dl) ─▶ ffmpeg ─┬─▶ 16 kHz WAV ─▶ faster-whisper ──────┐
                                                └─▶ ≤12 key frames ─▶ Claude (vision) ─┤
                               caption + transcript + frame notes ─▶ Claude (summary) ─┴─▶ JSON ─▶ SQLite cache
```

- **Frames.** ffmpeg picks frames at scene changes (`select='gt(scene,0.3)'`), at most 12. If it finds fewer than 4, it adds one frame every 5 seconds.
- **Two Claude calls per new post.** The first reads the frames: on-screen text and a one-line visual note for each. The second writes the summary. Both calls force a tool call, so the output always matches the schema.
- **Caching.** Results are cached by platform plus post ID. `/reel/X`, `/reels/X?igsh=…` and `/p/X` all hit the same entry. A link that has already been processed returns immediately at no cost. Use `force` or `/force` to reprocess.
- **Cleanup.** Media goes into a temporary folder that is deleted after every job, whether it succeeded or failed. Only the JSON is kept.
- **Queue.** One worker processes jobs in order. If the same link is sent twice while it's still running, it is processed once.
- **Limits.** Download: 120 s. Whole job: 10 min. Video length: 10 min. All three are configurable in `.env`.

## Setup (VPS with Docker)

```bash
git clone https://github.com/Awarently/instareader.git && cd instareader
cp .env.example .env            # fill in ANTHROPIC_API_KEY and TELEGRAM_BOT_TOKEN and/or HTTP_TOKEN
mkdir -p data cookies && sudo chown 1000:1000 data   # container runs as uid 1000
# put cookies.txt in ./cookies/ (next section)
docker compose up -d --build
docker compose logs -f
```

The first video you process downloads the Whisper model into `./data/models`. The default `small` model is about 480 MB, so the first job takes longer.

**Telegram:**

1. Create a new bot with @BotFather. Don't reuse the token of a bot that's already running, because Telegram allows only one poller per token.
2. Put the token in `.env`.
3. Leave `TELEGRAM_ALLOWED_CHAT_IDS` empty and start the service.
4. Message the bot. It replies with your chat ID.
5. Paste that ID into `.env` and run `docker compose up -d`.

**HTTP:** the port is bound to `127.0.0.1:8080` only. To reach it from outside, put a reverse proxy (Caddy or nginx) in front of it.

## Instagram cookies

Instagram refuses most anonymous downloads, so the downloaders log in using a browser cookie file.

1. Use a **throwaway Instagram account**, not your main one. Instagram flags scraping, and the account may get locked.
2. Log into that account in Chrome or Firefox.
3. Install a cookie exporter that writes Netscape format, such as "Get cookies.txt LOCALLY" (Chrome) or "cookies.txt" (Firefox).
4. With instagram.com open, export the cookies and save the file as `./cookies/cookies.txt`.
5. Restart the service with `docker compose restart`.

Cookies expire every few weeks. When that happens the bot replies with *"Instagram wants a login…"*. Re-export the cookies and restart. A private post only works if the throwaway account follows the creator.

## Testing

**Unit and pipeline tests.** These need no network, no API key, and no Whisper model. They use real ffmpeg on a generated clip, with the downloader, Whisper, and Claude faked.

```bash
pip install -r requirements-dev.txt
pytest -q
```

**Live check** once the service is running:

```bash
curl -s localhost:8080/health
curl -s -X POST localhost:8080/read \
  -H "Authorization: Bearer $HTTP_TOKEN" -H 'content-type: application/json' \
  -d '{"url":"https://www.instagram.com/reel/SHORTCODE/"}' | jq '.title_guess, .summary'
```

The call blocks until the job finishes, usually 1–3 minutes on a 2-vCPU VPS. Add `"force": true` to skip the cache.

## API

`POST /read`, with header `Authorization: Bearer <HTTP_TOKEN>` and body `{"url": "...", "force": false}`.

A successful call returns:

```json
{
  "title_guess": "…", "creator": "…",
  "summary": ["≤5 bullets"], "claims": [], "tools_or_products_mentioned": [],
  "links_mentioned": [], "action_items": [],
  "full_transcript": "…", "onscreen_text": "…",
  "visual_notes": [{"t": 3.2, "onscreen_text": "…", "visual": "…"}],
  "source": {"url": "…", "platform": "instagram", "caption": "…", "posted": "2026-10-05",
             "duration": 41.5, "downloader": "yt-dlp", "language": "en"},
  "processed_at": "…", "model": "claude-sonnet-5-5", "cached": false
}
```

Errors return `{"error": code, "message": text}`:

| code | HTTP | meaning |
|---|---|---|
| `unsupported_url` | 400 | Not a supported platform or link type |
| `private` | 403 | Private or deleted post |
| `too_long` | 413 | Longer than `MAX_VIDEO_SECONDS` |
| `no_media` | 422 | No video or images found |
| `login_required` | 424 | Cookies missing or expired |
| `download_failed` | 502 | Any other downloader failure (message includes the tail of its output) |
| `timeout` | 504 | Download or job timeout |
| `processing_failed` | 500 | ffmpeg, Whisper, or Claude error |

`GET /health` needs no auth and returns the queue size and the number of cached results.

## Cost

- **Claude:** two calls per new post. The vision call with up to 12 frames at 768 px is most of the cost. Cached posts cost nothing.
- **Whisper:** local faster-whisper is free and runs on the CPU. `WHISPER_API=1` switches to OpenAI's API, which is billed per minute of audio.

## Updating the downloaders

Instagram changes often and yt-dlp and gallery-dl follow quickly. If downloads start failing even though the cookies are fresh, rebuild the image to pull the latest versions:

```bash
docker compose build --no-cache && docker compose up -d
```

## Notes

Downloading from Instagram violates its terms of service. For personal use, the practical risk is that the scraping account gets locked.
