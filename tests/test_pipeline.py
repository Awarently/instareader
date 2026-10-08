"""End-to-end through the real ffmpeg steps and ClaudeClient code, with download, Whisper and the
Anthropic API faked. Also covers the JSON schema, cache hits and media cleanup."""
import asyncio
import shutil

import jsonschema
import pytest

from instareader.cache import Cache
from instareader.claude import ClaudeClient
from instareader.errors import ReelError
from instareader.fetch import Fetched
from instareader.media import Frame, merge_frames
from instareader.pipeline import Pipeline
from instareader.schema import RESULT_SCHEMA
from instareader.transcribe import Transcript
from instareader.worker import JobQueue

URL = "https://www.instagram.com/reel/Dd6ZIeCEzmh/?igsh=abc"


def make_pipeline(settings, fake_anthropic, sample_video, fetch_calls):
    async def fake_fetch(target, work, s):
        fetch_calls.append(target.url)
        dst = work / "media.mp4"
        shutil.copy(sample_video, dst)
        return Fetched(video=dst, caption="Try ToolX https://toolx.dev #ai", creator="someuser",
                       posted="2026-10-05", duration=9.0, downloader="fake")

    async def fake_transcribe(wav, s):
        assert wav.exists() and wav.stat().st_size > 0
        return Transcript("hello this is the transcript", "en")

    return Pipeline(settings, claude=ClaudeClient(settings, client=fake_anthropic),
                    fetcher=fake_fetch, transcriber=fake_transcribe)


def test_end_to_end_schema_cache_cleanup(settings, fake_anthropic, sample_video):
    fetch_calls = []

    async def go():
        jobs = JobQueue(settings, make_pipeline(settings, fake_anthropic, sample_video, fetch_calls),
                        Cache(settings.data_dir / "cache.sqlite"))
        jobs.start()
        first = await jobs.submit(URL)
        second = await jobs.submit("https://instagram.com/reels/Dd6ZIeCEzmh")  # same post, other form
        forced = await jobs.submit(URL, force=True)
        await jobs.stop()
        return first, second, forced

    first, second, forced = asyncio.run(go())

    jsonschema.validate(first, RESULT_SCHEMA)
    assert first["cached"] is False and second["cached"] is True and forced["cached"] is False
    assert len(fetch_calls) == 2  # cache hit skipped the download; force didn't
    assert first["full_transcript"] == "hello this is the transcript"
    assert first["source"]["platform"] == "instagram"
    assert first["source"]["duration"] == pytest.approx(9.0, abs=0.5)
    assert "https://toolx.dev" in first["links_mentioned"]
    assert first["onscreen_text"] == "STEP 1: install"  # repeated overlay de-duplicated
    # scene detection found the colour changes; frames are time-ordered
    ts = [n["t"] for n in first["visual_notes"]]
    assert len(ts) >= 3 and ts == sorted(ts)
    # images actually reached the vision call
    frame_call = fake_anthropic.messages.calls[0]
    assert sum(1 for b in frame_call["messages"][0]["content"] if b["type"] == "image") == len(ts)
    # media deleted, only JSON kept
    assert list((settings.data_dir / "work").iterdir()) == []


def test_concurrent_duplicate_submits_run_once(settings, fake_anthropic, sample_video):
    fetch_calls = []

    async def go():
        jobs = JobQueue(settings, make_pipeline(settings, fake_anthropic, sample_video, fetch_calls),
                        Cache(settings.data_dir / "cache.sqlite"))
        jobs.start()
        a, b = await asyncio.gather(jobs.submit(URL), jobs.submit(URL))
        await jobs.stop()
        return a, b

    a, b = asyncio.run(go())
    assert len(fetch_calls) == 1
    assert a["title_guess"] == b["title_guess"] == "Test reel"


def test_too_long_is_rejected_and_not_cached(settings, fake_anthropic, sample_video):
    from dataclasses import replace
    short = replace(settings, max_video_seconds=5)
    fetch_calls = []

    async def go():
        jobs = JobQueue(short, make_pipeline(short, fake_anthropic, sample_video, fetch_calls),
                        Cache(short.data_dir / "cache.sqlite"))
        jobs.start()
        try:
            with pytest.raises(ReelError) as e:
                await jobs.submit(URL)
            return e.value, jobs.cache.count()
        finally:
            await jobs.stop()

    err, cached = asyncio.run(go())
    assert err.code == "too_long" and cached == 0


def test_merge_frames_tops_up_and_caps(tmp_path):
    scene = [Frame(tmp_path / "s1", 7.0)]
    interval = [Frame(tmp_path / f"i{i}", float(i * 5)) for i in range(4)]  # 0,5,10,15
    merged = merge_frames(scene, interval, cap=12, min_scene=4)
    assert [f.t for f in merged] == [0.0, 5.0, 7.0, 10.0, 15.0]
    many = [Frame(tmp_path / f"s{i}", float(i)) for i in range(30)]
    assert len(merge_frames(many, [], cap=12, min_scene=4)) == 12
