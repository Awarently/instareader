"""Download a post: yt-dlp first, gallery-dl as fallback (also covers image-only posts)."""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import errors
from .config import Settings
from .errors import ReelError
from .proc import ProcTimeout, run
from .urls import ParsedUrl

log = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


@dataclass
class Fetched:
    video: Path | None
    images: list[Path] = field(default_factory=list)
    caption: str = ""
    creator: str = ""
    posted: str | None = None  # ISO date
    duration: float | None = None
    title: str = ""
    downloader: str = ""


def classify(output: str) -> ReelError | None:
    """Map downloader output to a user-facing error, or None if it isn't a known case."""
    low = output.lower()
    if "does not pass filter" in low:
        return errors.too_long(None, 0)  # caller re-raises with the real limit
    if "unsupported url" in low:
        # Logged-out requests often get redirected to a login/checkpoint page, which the
        # downloader then reports as an "unsupported URL".
        if "/login" in low or "checkpoint" in low or "login.php" in low:
            return errors.login_required()
        return ReelError("download_failed", _tail(output))
    if "private" in low and ("account" in low or "post" in low or "video" in low):
        return errors.private_post()
    if any(
        s in low
        for s in (
            "login required",
            "rate-limit reached or login required",
            "requested content is not available",
            "cookies",
            "log in",
            "401",
            "checkpoint_required",
        )
    ):
        return errors.login_required()
    if "404" in low or "not found" in low or "has been removed" in low or "deleted" in low:
        return errors.private_post()
    return None


def _iso_date(v: object) -> str | None:
    """yt-dlp gives YYYYMMDD or a unix timestamp; gallery-dl gives 'YYYY-MM-DD HH:MM:SS'."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc).date().isoformat()
    s = str(v).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10] if len(s) >= 10 and s[4] == "-" else None


def duration_filter(limit: int) -> str:
    """yt-dlp --match-filter: skip videos longer than `limit`; `?` lets posts with no duration through."""
    return f"duration<=?{limit}"


def _limit_error(limit: int, seconds: float | None = None) -> ReelError:
    return errors.too_long(seconds, limit)


async def _yt_dlp(target: ParsedUrl, work: Path, s: Settings) -> Fetched:
    args = [
        "yt-dlp",
        "--no-playlist",
        "--no-progress",
        "--quiet",
        "--no-warnings",
        "--restrict-filenames",
        "--socket-timeout", "30",
        "-f", "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
        "--merge-output-format", "mp4",
        "--match-filter", duration_filter(s.max_video_seconds),
        "--write-info-json",
        "-o", str(work / "media.%(ext)s"),
    ]
    if s.cookies_path:
        args += ["--cookies", str(s.cookies_path)]
    args.append(target.url)
    try:
        res = await run(*args, timeout=s.download_timeout)
    except ProcTimeout:
        raise ReelError("timeout", f"Download took longer than {s.download_timeout}s.")

    if "does not pass filter" in res.output.lower():
        raise _limit_error(s.max_video_seconds)
    videos = sorted(p for p in work.iterdir() if p.suffix.lower() in VIDEO_EXT)
    if res.returncode != 0 or not videos:
        err = classify(res.output)
        if err and err.code == "too_long":
            raise _limit_error(s.max_video_seconds)
        raise err or ReelError("download_failed", _tail(res.output))

    info = {}
    info_file = next(work.glob("*.info.json"), None)
    if info_file:
        info = json.loads(info_file.read_text())
    return Fetched(
        video=videos[0],
        caption=info.get("description") or "",
        creator=info.get("uploader") or info.get("channel") or info.get("uploader_id") or "",
        posted=_iso_date(info.get("upload_date") or info.get("timestamp")),
        duration=info.get("duration"),
        title=info.get("title") or "",
        downloader="yt-dlp",
    )


async def _gallery_dl(target: ParsedUrl, work: Path, s: Settings) -> Fetched:
    out = work / "gdl"
    out.mkdir(exist_ok=True)
    args = ["gallery-dl", "--quiet", "--write-metadata", "-D", str(out)]
    if s.cookies_path:
        args += ["--cookies", str(s.cookies_path)]
    args.append(target.url)
    try:
        res = await run(*args, timeout=s.download_timeout)
    except ProcTimeout:
        raise ReelError("timeout", f"Download took longer than {s.download_timeout}s.")

    files = sorted(p for p in out.iterdir() if p.is_file())
    videos = [p for p in files if p.suffix.lower() in VIDEO_EXT]
    images = [p for p in files if p.suffix.lower() in IMAGE_EXT]
    if not videos and not images:
        err = classify(res.output)
        raise err or ReelError("download_failed", _tail(res.output))

    meta: dict = {}
    for mf in sorted(out.glob("*.json")):
        try:
            meta = json.loads(mf.read_text())
            break
        except ValueError:
            continue
    author = meta.get("author") if isinstance(meta.get("author"), dict) else {}
    return Fetched(
        video=videos[0] if videos else None,
        images=images if not videos else [],
        caption=meta.get("description") or meta.get("content") or "",
        creator=meta.get("username") or meta.get("owner_username") or author.get("name") or "",
        posted=_iso_date(meta.get("post_date") or meta.get("date")),
        duration=meta.get("video_duration"),
        downloader="gallery-dl",
    )


def _tail(text: str, n: int = 300) -> str:
    text = " ".join(text.split())
    return "Download failed: " + (text[-n:] if text else "no output from downloader.")


async def fetch(target: ParsedUrl, work: Path, s: Settings) -> Fetched:
    try:
        return await _yt_dlp(target, work, s)
    except ReelError as first:
        # Final answers: don't hammer Instagram a second time with another tool.
        if first.code in {"too_long", "timeout"}:
            raise
        if not shutil.which("gallery-dl"):
            raise
        log.info("yt-dlp failed (%s), trying gallery-dl", first.code)
        try:
            return await _gallery_dl(target, work, s)
        except ReelError as second:
            # Prefer a specific diagnosis (login/private) from either tool; otherwise yt-dlp's
            # message, which is the more informative of the two.
            if first.code == "download_failed" and second.code != "download_failed":
                raise second
            raise first
