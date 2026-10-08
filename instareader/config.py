"""Settings, read once from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v and v.strip() else default


def _float(name: str, default: float) -> float:
    v = os.getenv(name)
    return float(v) if v and v.strip() else default


def _ids(name: str) -> frozenset[int]:
    raw = os.getenv(name, "")
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


@dataclass(frozen=True)
class Settings:
    # Claude
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-5-5"
    # Transcription
    whisper_model: str = "small"
    whisper_api: bool = False
    openai_api_key: str = ""
    # Fetching
    ig_cookies: str = ""
    # Storage
    data_dir: Path = Path("/data")
    # Interfaces
    http_token: str = ""
    http_host: str = "0.0.0.0"
    http_port: int = 8080
    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: frozenset[int] = field(default_factory=frozenset)
    # Limits
    download_timeout: int = 120
    job_timeout: int = 600
    max_video_seconds: int = 600
    max_frames: int = 12
    min_scene_frames: int = 4
    interval_seconds: int = 5
    scene_threshold: float = 0.3

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            claude_model=os.getenv("CLAUDE_MODEL") or cls.claude_model,
            whisper_model=os.getenv("WHISPER_MODEL") or cls.whisper_model,
            whisper_api=_bool("WHISPER_API"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            ig_cookies=os.getenv("IG_COOKIES", ""),
            data_dir=Path(os.getenv("DATA_DIR") or "/data"),
            http_token=os.getenv("HTTP_TOKEN", ""),
            http_host=os.getenv("HTTP_HOST") or "0.0.0.0",
            http_port=_int("HTTP_PORT", 8080),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_allowed_chat_ids=_ids("TELEGRAM_ALLOWED_CHAT_IDS"),
            download_timeout=_int("DOWNLOAD_TIMEOUT", 120),
            job_timeout=_int("JOB_TIMEOUT", 600),
            max_video_seconds=_int("MAX_VIDEO_SECONDS", 600),
            max_frames=_int("MAX_FRAMES", 12),
            scene_threshold=_float("SCENE_THRESHOLD", 0.3),
        )

    @property
    def cookies_path(self) -> Path | None:
        if self.ig_cookies and Path(self.ig_cookies).is_file():
            return Path(self.ig_cookies)
        return None
