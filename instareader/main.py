"""Entry point: one process runs the worker, the HTTP API and (if configured) the Telegram bot."""
from __future__ import annotations

import asyncio
import logging
import shutil
import sys

import uvicorn

from .api import create_app
from .cache import Cache
from .config import Settings
from .pipeline import Pipeline
from .telegram_bot import TelegramBot
from .worker import JobQueue

log = logging.getLogger("instareader")


def _preflight(s: Settings) -> None:
    missing = [t for t in ("ffmpeg", "ffprobe", "yt-dlp") if not shutil.which(t)]
    if missing:
        sys.exit(f"Missing required tools on PATH: {', '.join(missing)}")
    if not s.anthropic_api_key:
        sys.exit("ANTHROPIC_API_KEY is not set.")
    if not s.http_token and not s.telegram_bot_token:
        sys.exit("Set HTTP_TOKEN and/or TELEGRAM_BOT_TOKEN; with neither there is no way to use the service.")
    if s.ig_cookies and not s.cookies_path:
        log.warning("IG_COOKIES=%s does not exist; Instagram posts behind login will fail.", s.ig_cookies)
    if s.telegram_bot_token and not s.telegram_allowed_chat_ids:
        log.warning("TELEGRAM_ALLOWED_CHAT_IDS is empty: the bot will refuse everyone and reply with their chat ID.")


async def amain() -> None:
    s = Settings.from_env()
    _preflight(s)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    jobs = JobQueue(s, Pipeline(s), Cache(s.data_dir / "cache.sqlite"))
    jobs.start()

    # HTTP always runs so /health works for the container healthcheck; /read answers 503 without HTTP_TOKEN.
    server = uvicorn.Server(uvicorn.Config(
        create_app(jobs, s.http_token), host=s.http_host, port=s.http_port,
        log_level="info", access_log=False,
    ))
    tasks = [asyncio.create_task(server.serve(), name="http")]
    bot = TelegramBot(s, jobs) if s.telegram_bot_token else None
    if bot:
        tasks.append(asyncio.create_task(bot.run(), name="telegram"))

    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if t.exception():
                raise t.exception()
    finally:
        for t in tasks:
            t.cancel()
        if bot:
            await bot.close()
        await jobs.stop()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
