from fastapi.testclient import TestClient

from instareader.api import create_app
from instareader.cache import Cache
from instareader.telegram_bot import format_summary, transcript_file
from instareader.urls import parse
from instareader.worker import JobQueue

RESULT = {
    "title_guess": "Claude <tips> & tricks", "creator": "someuser",
    "summary": ["one", "two"], "claims": [], "tools_or_products_mentioned": ["Claude Code"],
    "links_mentioned": [], "action_items": ["Install it"], "full_transcript": "spoken words",
    "onscreen_text": "STEP 1", "visual_notes": [{"t": 1.5, "onscreen_text": "STEP 1", "visual": "a terminal"}],
    "source": {"url": "https://www.instagram.com/reel/abc123/", "platform": "instagram", "caption": "cap",
               "posted": "2026-10-05", "duration": 65.0, "downloader": "yt-dlp", "language": "en"},
    "processed_at": "2026-10-08T17:00:00+00:00", "model": "m",
}


def test_http_auth_and_cached_read(settings):
    cache = Cache(settings.data_dir / "c.sqlite")
    cache.put(parse(RESULT["source"]["url"]).cache_key, RESULT["source"]["url"], RESULT)
    jobs = JobQueue(settings, pipeline=None, cache=cache)  # cache hit never touches the pipeline
    client = TestClient(create_app(jobs, "secret"))

    assert client.post("/read", json={"url": RESULT["source"]["url"]}).status_code == 401
    assert client.post("/read", json={"url": "x"}, headers={"Authorization": "Bearer wrong"}).status_code == 401

    ok = client.post("/read", json={"url": "https://instagram.com/reels/abc123"},
                     headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200 and ok.json()["cached"] is True

    bad = client.post("/read", json={"url": "https://example.com/v"}, headers={"Authorization": "Bearer secret"})
    assert bad.status_code == 400 and bad.json()["error"] == "unsupported_url"
    assert client.get("/health").json()["cached_results"] == 1


def test_telegram_formatting_escapes_html():
    text = format_summary({**RESULT, "cached": True})
    assert "Claude &lt;tips&gt; &amp; tricks" in text
    assert "1:05" in text and "Saved result" in text
    assert "<b>Tools / products</b>" in text and "Claims" not in text  # empty sections omitted
    doc = transcript_file(RESULT)
    assert "TRANSCRIPT\nspoken words" in doc and "[1.5s] a terminal" in doc


def test_web_page_and_background_jobs(settings, fake_anthropic, sample_video):
    import time

    from fastapi.testclient import TestClient

    from tests.test_pipeline import make_pipeline

    calls = []
    jobs = JobQueue(settings, make_pipeline(settings, fake_anthropic, sample_video, calls),
                    Cache(settings.data_dir / "c.sqlite"))
    app = create_app(jobs, "secret")

    @app.on_event("startup")
    async def _start():
        jobs.start()

    h = {"Authorization": "Bearer secret"}
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200 and "Insta <span>Reader</span>" in page.text
        assert client.post("/api/jobs", json={"url": "https://instagram.com/reel/abc"}).status_code == 401

        bad = client.post("/api/jobs", json={"url": "https://example.com/v"}, headers=h)
        assert bad.status_code == 400 and bad.json()["status"] == "error"

        started = client.post("/api/jobs", json={"url": "https://www.instagram.com/reel/abc123/"}, headers=h).json()
        assert started["status"] in {"queued", "running"}
        for _ in range(100):
            st = client.get(f"/api/jobs/{started['id']}", headers=h).json()
            if st["status"] == "done":
                break
            time.sleep(0.1)
        assert st["status"] == "done" and st["result"]["title_guess"] == "Test reel"

        again = client.post("/api/jobs", json={"url": "https://instagram.com/reels/abc123?igsh=x"}, headers=h).json()
        assert again["status"] == "done" and again["result"]["cached"] is True
        assert len(calls) == 1

        recent = client.get("/api/recent", headers=h).json()
        assert recent[0]["id"] == started["id"] and recent[0]["title"] == "Test reel"
        assert client.get("/api/jobs/" + "0" * 64, headers=h).status_code == 404
