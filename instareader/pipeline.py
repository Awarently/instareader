"""One post in, one result dict out. Media lives in a temp dir that is always deleted."""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import media
from .claude import ClaudeClient
from .config import Settings
from .errors import ReelError, too_long
from .fetch import fetch
from .transcribe import transcribe
from .urls import ParsedUrl

log = logging.getLogger(__name__)
_LINK = re.compile(r"https?://[^\s<>\"')]+")


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for x in items:
        k = x.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(x.strip())
    return out


def _onscreen(notes: list[dict]) -> str:
    """Unique on-screen text blocks in time order (overlays often repeat across frames)."""
    return "\n".join(_dedupe([n["onscreen_text"] for n in notes if n.get("onscreen_text")]))


class Pipeline:
    def __init__(self, s: Settings, claude: ClaudeClient | None = None,
                 fetcher=fetch, transcriber=transcribe):
        self.s = s
        self._claude = claude
        self.fetcher = fetcher
        self.transcriber = transcriber

    @property
    def claude(self) -> ClaudeClient:
        if self._claude is None:
            self._claude = ClaudeClient(self.s)
        return self._claude

    async def run(self, target: ParsedUrl) -> dict:
        work_root = self.s.data_dir / "work"
        work_root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="job-", dir=work_root))
        try:
            return await self._run(target, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    async def _run(self, target: ParsedUrl, work: Path) -> dict:
        s = self.s
        log.info("fetch %s", target.url)
        got = await self.fetcher(target, work, s)

        transcript_text, language, frames = "", None, []
        dur = got.duration
        if got.video:
            dur = await media.duration(got.video) or dur
            if dur and dur > s.max_video_seconds:
                raise too_long(dur, s.max_video_seconds)
            if await media.has_audio(got.video):
                wav = await media.extract_audio(got.video, work / "audio.wav")
                log.info("transcribe %.0fs of audio", dur or 0)
                tr = await self.transcriber(wav, s)
                transcript_text, language = tr.text, tr.language
            frames = await media.key_frames(
                got.video, work / "frames", threshold=s.scene_threshold, cap=s.max_frames,
                min_scene=s.min_scene_frames, every=s.interval_seconds,
            )
        elif got.images:
            frames = await media.still_frames(got.images, work / "frames", s.max_frames)
        if not got.video and not frames:
            raise ReelError("no_media", "The post had no video or images to read.")

        log.info("vision on %d frames", len(frames))
        notes = await self.claude.read_frames(frames)
        summary = await self.claude.summarize(
            platform=target.platform, creator=got.creator, caption=got.caption,
            transcript=transcript_text, frame_notes=notes,
        )

        caption_links = _LINK.findall(got.caption or "")
        return {
            "title_guess": summary.get("title_guess") or got.title or "",
            "creator": summary.get("creator") or got.creator or "",
            "summary": list(summary.get("summary") or [])[:5],
            "claims": list(summary.get("claims") or []),
            "tools_or_products_mentioned": list(summary.get("tools_or_products_mentioned") or []),
            "links_mentioned": _dedupe(caption_links + list(summary.get("links_mentioned") or [])),
            "action_items": list(summary.get("action_items") or []),
            "full_transcript": transcript_text,
            "onscreen_text": _onscreen(notes),
            "visual_notes": notes,
            "source": {
                "url": target.url,
                "platform": target.platform,
                "caption": got.caption or "",
                "posted": got.posted,
                "duration": round(dur, 1) if dur else None,
                "downloader": got.downloader,
                "language": language,
            },
            "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": s.claude_model,
        }
