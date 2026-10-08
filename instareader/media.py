"""ffmpeg/ffprobe helpers: duration, audio extraction, key-frame selection."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import ReelError
from .proc import run

FRAME_SCALE = "scale='min(768,iw)':-2"
_PTS = re.compile(r"pts_time:\s*([0-9.]+)")


@dataclass
class Frame:
    path: Path
    t: float | None  # seconds into the video; None for still images


async def duration(video: Path) -> float | None:
    r = await run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video), timeout=30)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


async def has_audio(video: Path) -> bool:
    r = await run(
        "ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0",
        str(video), timeout=30,
    )
    return bool(r.stdout.strip())


async def extract_audio(video: Path, wav: Path) -> Path:
    r = await run(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav), timeout=300,
    )
    if r.returncode != 0 or not wav.exists():
        raise ReelError("processing_failed", f"Audio extraction failed: {r.stderr.strip()[-200:]}")
    return wav


async def _scene_frames(video: Path, out: Path, threshold: float, cap: int) -> list[Frame]:
    r = await run(
        "ffmpeg", "-hide_banner", "-y", "-i", str(video),
        "-vf", f"select='gt(scene,{threshold})',showinfo,{FRAME_SCALE}",
        "-fps_mode", "vfr", "-frames:v", str(cap), "-q:v", "3",
        str(out / "scene_%03d.jpg"), timeout=300,
    )
    times = [float(x) for x in _PTS.findall(r.stderr)]
    files = sorted(out.glob("scene_*.jpg"))
    return [Frame(p, times[i] if i < len(times) else None) for i, p in enumerate(files)]


async def _interval_frames(video: Path, out: Path, every: int, cap: int) -> list[Frame]:
    await run(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
        "-vf", f"fps=1/{every},{FRAME_SCALE}", "-frames:v", str(cap), "-q:v", "3",
        str(out / "int_%03d.jpg"), timeout=300,
    )
    return [Frame(p, float(i * every)) for i, p in enumerate(sorted(out.glob("int_*.jpg")))]


def merge_frames(scene: list[Frame], interval: list[Frame], cap: int, min_scene: int, gap: float = 1.5) -> list[Frame]:
    """Scene-change frames; topped up with interval frames when scene detection finds fewer than `min_scene`.
    Interval frames within `gap` seconds of a scene frame are dropped as near-duplicates; result is
    time-ordered and evenly thinned to `cap`."""
    frames = list(scene)
    if len(scene) < min_scene:
        scene_t = [f.t for f in scene if f.t is not None]
        frames += [f for f in interval if f.t is None or all(abs(f.t - t) > gap for t in scene_t)]
    frames.sort(key=lambda f: (f.t is None, f.t or 0.0))
    if len(frames) > cap:
        step = len(frames) / cap
        frames = [frames[int(i * step)] for i in range(cap)]
    return frames


async def key_frames(video: Path, out: Path, *, threshold: float, cap: int, min_scene: int, every: int) -> list[Frame]:
    out.mkdir(parents=True, exist_ok=True)
    scene = await _scene_frames(video, out, threshold, cap)
    interval = await _interval_frames(video, out, every, cap) if len(scene) < min_scene else []
    frames = merge_frames(scene, interval, cap, min_scene)
    if not frames:  # very short clip with no scene change
        first = out / "first.jpg"
        await run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                  "-vf", FRAME_SCALE, "-frames:v", "1", str(first), timeout=60)
        if first.exists():
            frames = [Frame(first, 0.0)]
    return frames


async def still_frames(images: list[Path], out: Path, cap: int) -> list[Frame]:
    """Carousel/image posts: downscale to JPEG so the vision call stays small."""
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, img in enumerate(images[:cap]):
        dst = out / f"img_{i:03d}.jpg"
        r = await run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(img),
                      "-vf", FRAME_SCALE, "-frames:v", "1", "-q:v", "3", str(dst), timeout=60)
        if r.returncode == 0 and dst.exists():
            frames.append(Frame(dst, None))
    return frames
