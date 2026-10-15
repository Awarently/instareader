"""User-facing failures. `code` is stable for API clients; `message` is shown to the user."""
from __future__ import annotations

HTTP_STATUS = {
    "unsupported_url": 400,
    "private": 403,
    "login_required": 424,
    "too_long": 413,
    "no_media": 422,
    "timeout": 504,
    "download_failed": 502,
    "processing_failed": 500,
}


class ReelError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 500)

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message}


def unsupported(url: str) -> ReelError:
    return ReelError(
        "unsupported_url",
        f"Not a supported link: {url}. Send an Instagram reel/post, TikTok, YouTube, X or Facebook video URL.",
    )


def login_required() -> ReelError:
    return ReelError(
        "login_required",
        "The site wants a login for this post. The cookies file is missing or expired: "
        "export cookies.txt from a throwaway account on that site (Instagram, Facebook, ...), "
        "put it in the cookies folder and restart the service.",
    )


def private_post() -> ReelError:
    return ReelError(
        "private",
        "This post is private or was deleted. The scraping account must follow the creator to read it.",
    )


def too_long(seconds: float | None, limit: int) -> ReelError:
    got = f" ({int(seconds // 60)}m{int(seconds % 60):02d}s)" if seconds else ""
    return ReelError(
        "too_long",
        f"Video is longer than the {limit // 60}-minute limit{got}. Raise MAX_VIDEO_SECONDS to allow it.",
    )
