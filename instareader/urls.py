"""Find, validate and canonicalize social video URLs.

The cache key is platform + content id, so /reel/X, /reels/X and /p/X?igsh=...
all map to the same entry.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import unsupported

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("instagram", re.compile(r"^(?:www\.|m\.)?instagram\.com$")),
    ("tiktok", re.compile(r"^(?:www\.|m\.|vm\.|vt\.)?tiktok\.com$")),
    ("youtube", re.compile(r"^(?:www\.|m\.)?(?:youtube\.com|youtu\.be)$")),
    ("x", re.compile(r"^(?:www\.|mobile\.)?(?:x\.com|twitter\.com)$")),
    ("facebook", re.compile(r"^(?:www\.|m\.|web\.)?(?:facebook\.com|fb\.watch)$")),
]

_IG_PATH = re.compile(r"^/(?:[A-Za-z0-9_.]+/)?(reels?|p|tv)/([A-Za-z0-9_-]+)")
_TT_VIDEO = re.compile(r"^/@([^/]+)/(?:video|photo)/(\d+)")
_TT_SHORT = re.compile(r"^/(?:t/)?([A-Za-z0-9]+)/?$")
_YT_SHORTS = re.compile(r"^/shorts/([A-Za-z0-9_-]{6,})")
_YT_WATCH_ID = re.compile(r"(?:^|&)v=([A-Za-z0-9_-]{6,})")
_YTB = re.compile(r"^/([A-Za-z0-9_-]{6,})")
_X_STATUS = re.compile(r"^/([^/]+)/status/(\d+)")
_FB_REEL = re.compile(r"^/reel/(\d+)")
_FB_VIDEOS = re.compile(r"^/[^/]+/videos/(?:[^/]+/)?(\d+)")
_FB_SHARE = re.compile(r"^/share/(?:r|v)/([A-Za-z0-9_-]+)")
_FB_WATCH_ID = re.compile(r"(?:^|&)v=(\d+)")
_FBW = re.compile(r"^/([A-Za-z0-9_-]+)")


@dataclass(frozen=True)
class ParsedUrl:
    platform: str
    content_id: str
    url: str  # canonical URL passed to downloaders

    @property
    def cache_key(self) -> str:
        return hashlib.sha256(f"{self.platform}:{self.content_id}".encode()).hexdigest()


def find_url(text: str) -> str | None:
    """First http(s) URL in free text (e.g. a Telegram message), trailing punctuation stripped."""
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(").,!?;:'\"") if m else None


def parse(url: str) -> ParsedUrl:
    raw = (url or "").strip()
    if raw and not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    platform = next((name for name, rx in _PATTERNS if rx.match(host)), None)

    if platform == "instagram":
        m = _IG_PATH.match(path)
        if m:
            kind = "p" if m.group(1) == "p" else ("tv" if m.group(1) == "tv" else "reel")
            code = m.group(2)
            return ParsedUrl("instagram", code, f"https://www.instagram.com/{kind}/{code}/")
    elif platform == "tiktok":
        m = _TT_VIDEO.match(path)
        if m:
            return ParsedUrl("tiktok", m.group(2), f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}")
        if host.startswith(("vm.", "vt.")) or path.startswith("/t/"):
            m = _TT_SHORT.match(path)
            if m:  # short link: id is the short code, downloader follows the redirect
                return ParsedUrl("tiktok", "s-" + m.group(1), f"https://{host}{path}")
    elif platform == "youtube":
        vid = None
        if host.endswith("youtu.be"):
            m = _YTB.match(path)
            vid = m.group(1) if m else None
        else:
            m = _YT_SHORTS.match(path)
            if m:
                vid = m.group(1)
            elif path == "/watch":
                m = _YT_WATCH_ID.search(parts.query)
                vid = m.group(1) if m else None
        if vid:
            return ParsedUrl("youtube", vid, f"https://www.youtube.com/shorts/{vid}")
    elif platform == "x":
        m = _X_STATUS.match(path)
        if m:
            return ParsedUrl("x", m.group(2), f"https://x.com/{m.group(1)}/status/{m.group(2)}")
    elif platform == "facebook":
        if host == "fb.watch":
            m = _FBW.match(path)
            if m:  # short link: downloader follows the redirect
                return ParsedUrl("facebook", "s-" + m.group(1), f"https://fb.watch/{m.group(1)}/")
        elif m := _FB_REEL.match(path):
            return ParsedUrl("facebook", m.group(1), f"https://www.facebook.com/reel/{m.group(1)}")
        elif m := _FB_VIDEOS.match(path):
            return ParsedUrl("facebook", m.group(1), f"https://www.facebook.com/watch/?v={m.group(1)}")
        elif path.rstrip("/") == "/watch" and (m := _FB_WATCH_ID.search(parts.query)):
            return ParsedUrl("facebook", m.group(1), f"https://www.facebook.com/watch/?v={m.group(1)}")
        elif m := _FB_SHARE.match(path):  # share link: downloader follows the redirect
            return ParsedUrl("facebook", "s-" + m.group(1), f"https://www.facebook.com{path}")

    raise unsupported(url)
