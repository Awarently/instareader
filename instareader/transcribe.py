"""Speech-to-text: local faster-whisper by default, OpenAI Whisper API when WHISPER_API=1."""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import Settings
from .errors import ReelError

log = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()


@dataclass
class Transcript:
    text: str
    language: str | None = None


def _load_model(s: Settings):
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            log.info("Loading faster-whisper model %r (first run downloads it)", s.whisper_model)
            _model = WhisperModel(
                s.whisper_model, device="cpu", compute_type="int8", download_root=str(s.data_dir / "models")
            )
        return _model


def load_wav(wav: Path):
    """16 kHz mono PCM16 WAV (what media.extract_audio writes) -> float32 numpy array in [-1, 1].

    Passing samples instead of a path skips faster-whisper's PyAV decoder, which breaks on
    newer PyAV releases ("open() got an unexpected keyword argument 'metadata_errors'")."""
    import wave

    import numpy as np

    with wave.open(str(wav), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != 16000:
            raise ReelError("processing_failed",
                            f"Unexpected audio format: {w.getnchannels()}ch {w.getframerate()}Hz {8 * w.getsampwidth()}-bit")
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


def _local(wav: Path, s: Settings) -> Transcript:
    model = _load_model(s)
    segments, info = model.transcribe(load_wav(wav), vad_filter=True, beam_size=5)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return Transcript(text, info.language)


async def _api(wav: Path, s: Settings) -> Transcript:
    if not s.openai_api_key:
        raise ReelError("processing_failed", "WHISPER_API=1 but OPENAI_API_KEY is not set.")
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {s.openai_api_key}"},
            data={"model": "whisper-1", "response_format": "verbose_json"},
            files={"file": (wav.name, wav.read_bytes(), "audio/wav")},
        )
    if r.status_code != 200:
        raise ReelError("processing_failed", f"Whisper API error {r.status_code}: {r.text[:200]}")
    body = r.json()
    return Transcript((body.get("text") or "").strip(), body.get("language"))


async def transcribe(wav: Path, s: Settings) -> Transcript:
    if s.whisper_api:
        return await _api(wav, s)
    return await asyncio.to_thread(_local, wav, s)
