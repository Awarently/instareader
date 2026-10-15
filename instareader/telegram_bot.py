"""Telegram interface (long polling). Send a link; get a summary plus the transcript as a .txt file."""
from __future__ import annotations

import asyncio
import html
import logging

import httpx

from .config import Settings
from .errors import ReelError
from .urls import find_url
from .worker import JobQueue

log = logging.getLogger(__name__)
MAX_MSG = 4000  # Telegram limit is 4096

HELP = (
    "Send me an Instagram reel/post link (TikTok, YouTube, X and Facebook links work too).\n"
    "I reply with a summary and the full transcript as a text file.\n\n"
    "/force <link> reprocesses a link instead of using the saved result."
)


def _fmt_duration(sec: float | None) -> str | None:
    if not sec:
        return None
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def _section(title: str, items: list[str]) -> str:
    if not items:
        return ""
    lines = "\n".join(f"• {html.escape(x)}" for x in items)
    return f"\n\n<b>{title}</b>\n{lines}"


def format_summary(r: dict) -> str:
    src = r.get("source", {})
    meta = [x for x in (
        html.escape(r.get("creator") or ""), src.get("platform"), src.get("posted"),
        _fmt_duration(src.get("duration")),
    ) if x]
    text = f"<b>{html.escape(r.get('title_guess') or 'Untitled')}</b>\n{' · '.join(meta)}"
    text += _section("Summary", r.get("summary", []))
    text += _section("Claims", r.get("claims", []))
    text += _section("Tools / products", r.get("tools_or_products_mentioned", []))
    text += _section("Links", r.get("links_mentioned", []))
    text += _section("Action items", r.get("action_items", []))
    if r.get("cached"):
        text += "\n\n<i>Saved result. Send /force with the link to reprocess.</i>"
    if len(text) > MAX_MSG:
        text = text[: MAX_MSG - 20].rsplit("\n", 1)[0] + "\n…(truncated)"
    return text


def transcript_file(r: dict) -> str:
    src = r.get("source", {})
    parts = [
        f"{r.get('title_guess', '')}\n{src.get('url', '')}",
        f"CAPTION\n{src.get('caption') or '(none)'}",
        f"TRANSCRIPT\n{r.get('full_transcript') or '(no speech)'}",
        f"ON-SCREEN TEXT\n{r.get('onscreen_text') or '(none)'}",
    ]
    notes = r.get("visual_notes") or []
    if notes:
        parts.append("FRAMES\n" + "\n".join(
            (f"[{n['t']:.1f}s] " if n.get("t") is not None else "- ") + n.get("visual", "") for n in notes
        ))
    return "\n\n".join(parts) + "\n"


class TelegramBot:
    def __init__(self, s: Settings, jobs: JobQueue):
        self.s = s
        self.jobs = jobs
        self.base = f"https://api.telegram.org/bot{s.telegram_bot_token}"
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15))
        self._tasks: set[asyncio.Task] = set()

    async def _call(self, method: str, **kw) -> dict:
        r = await self.client.post(f"{self.base}/{method}", **kw)
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {body.get('description')}")
        return body.get("result")

    async def send(self, chat_id: int, text: str, reply_to: int | None = None) -> None:
        data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to:
            data["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        await self._call("sendMessage", json=data)

    async def send_text_file(self, chat_id: int, name: str, content: str) -> None:
        await self._call("sendDocument", data={"chat_id": str(chat_id)},
                         files={"document": (name, content.encode(), "text/plain")})

    async def run(self) -> None:
        me = await self._call("getMe")
        log.info("Telegram bot @%s polling", me.get("username"))
        offset = None
        while True:
            try:
                params = {"timeout": 50, "allowed_updates": '["message"]'}
                if offset is not None:
                    params["offset"] = offset
                r = await self.client.get(f"{self.base}/getUpdates", params=params, timeout=65)
                body = r.json()
                if not body.get("ok"):
                    log.error("getUpdates: %s", body.get("description"))
                    await asyncio.sleep(10)
                    continue
                for upd in body["result"]:
                    offset = upd["update_id"] + 1
                    msg = upd.get("message")
                    if msg and msg.get("text"):
                        t = asyncio.create_task(self._handle(msg))
                        self._tasks.add(t)
                        t.add_done_callback(self._tasks.discard)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("polling error; retrying in 5s")
                await asyncio.sleep(5)

    async def _handle(self, msg: dict) -> None:
        chat_id = msg["chat"]["id"]
        mid = msg.get("message_id")
        text = msg["text"].strip()
        try:
            if chat_id not in self.s.telegram_allowed_chat_ids:
                log.warning("message from unauthorized chat %s", chat_id)
                await self.send(chat_id, f"Not authorized. Your chat ID is <code>{chat_id}</code>; "
                                         "add it to TELEGRAM_ALLOWED_CHAT_IDS and restart the service.")
                return
            if text.startswith(("/start", "/help")):
                await self.send(chat_id, HELP)
                return
            force = text.startswith("/force")
            url = find_url(text)
            if not url:
                await self.send(chat_id, "Send a link to a reel or post.", mid)
                return
            try:
                _, hit = self.jobs.lookup(url)
            except ReelError as e:
                await self.send(chat_id, html.escape(e.message), mid)
                return
            if force or hit is None:
                ahead = self.jobs.pending
                note = f" ({ahead} ahead of you)" if ahead else ""
                await self.send(chat_id, f"Processing{note}. This usually takes 1–3 minutes.", mid)
            result = await self.jobs.submit(url, force=force)
            await self.send(chat_id, format_summary(result), mid)
            await self.send_text_file(chat_id, "transcript.txt", transcript_file(result))
        except ReelError as e:
            await self.send(chat_id, html.escape(e.message), mid)
        except Exception as e:
            log.exception("handler failed")
            try:
                await self.send(chat_id, f"Something went wrong: {html.escape(str(e))[:300]}", mid)
            except Exception:
                pass

    async def close(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await self.client.aclose()
