import pytest

from instareader.fetch import _iso_date, classify


@pytest.mark.parametrize("output,code", [
    ("ERROR: [Instagram] Dd6Z: Requested content is not available, rate-limit reached or login required. Use --cookies", "login_required"),
    ("ERROR: [Instagram] abc: This content may be inappropriate... login required", "login_required"),
    ("ERROR: [Instagram] abc: This account is private", "private"),
    ("ERROR: Unsupported URL: https://example.com", "download_failed"),
    ("ERROR: Unsupported URL: https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Freel%2F1", "login_required"),
    ("[download] Dd6Z does not pass filter (!duration | duration<=600), skipping ..", "too_long"),
    ("[instagram][error] HttpError: '404 Not Found' for 'https://www.instagram.com/p/x/'", "private"),
])
def test_classify(output, code):
    err = classify(output)
    assert err is not None and err.code == code


def test_classify_unknown():
    assert classify("ERROR: something odd happened") is None


@pytest.mark.parametrize("v,expected", [
    ("20261005", "2026-10-05"),
    (1759708800, "2025-10-06"),
    ("2026-10-05 14:22:01", "2026-10-05"),
    (None, None),
    ("", None),
])
def test_iso_date(v, expected):
    assert _iso_date(v) == expected


# --- fetch() against fake downloader binaries on PATH -------------------------------------------
import asyncio  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import stat  # noqa: E402

from instareader.errors import ReelError  # noqa: E402
from instareader.fetch import fetch  # noqa: E402
from instareader.urls import parse  # noqa: E402


def _fake_bin(dir_, name, script):
    p = dir_ / name
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def _with_path(monkeypatch, bindir):
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def test_fetch_login_required_survives_fallback(tmp_path, monkeypatch, settings):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake_bin(bindir, "yt-dlp", 'echo "ERROR: [Instagram] x: Requested content is not available, '
                                'rate-limit reached or login required" >&2; exit 1\n')
    _fake_bin(bindir, "gallery-dl", "exit 4\n")
    _with_path(monkeypatch, bindir)
    work = tmp_path / "w"
    work.mkdir()
    with pytest.raises(ReelError) as e:
        asyncio.run(fetch(parse("https://www.instagram.com/reel/abc/"), work, settings))
    assert e.value.code == "login_required"


def test_fetch_success_reads_info_json(tmp_path, monkeypatch, settings):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    info = json.dumps({"description": "cap #x", "uploader": "creator1", "upload_date": "20261001", "duration": 42})
    # last arg is the URL; the -o template's directory is where files must land
    _fake_bin(bindir, "yt-dlp", f"""
out=""; prev=""
for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
d=$(dirname "$out"); : > "$d/media.mp4"; echo '{info}' > "$d/media.info.json"
""")
    _with_path(monkeypatch, bindir)
    work = tmp_path / "w"
    work.mkdir()
    got = asyncio.run(fetch(parse("https://www.instagram.com/reel/abc/"), work, settings))
    assert got.video.name == "media.mp4"
    assert (got.caption, got.creator, got.posted, got.duration) == ("cap #x", "creator1", "2026-10-01", 42)


def test_duration_filter_is_valid_yt_dlp_syntax():
    from yt_dlp.utils import match_filter_func

    from instareader.fetch import duration_filter

    fn = match_filter_func(duration_filter(600))
    assert fn({"duration": 30}, incomplete=False) is None          # short: allowed
    assert fn({}, incomplete=False) is None                        # unknown length: allowed
    assert "does not pass filter" in fn({"duration": 700}, incomplete=False)  # too long: skipped
