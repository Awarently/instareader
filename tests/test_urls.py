import pytest

from instareader.errors import ReelError
from instareader.urls import find_url, parse


@pytest.mark.parametrize("url,platform,cid,canonical", [
    ("https://www.instagram.com/reel/Dd6ZIeCEzmh", "instagram", "Dd6ZIeCEzmh", "https://www.instagram.com/reel/Dd6ZIeCEzmh/"),
    ("https://instagram.com/reels/Dd6ZIeCEzmh/?igsh=abc123", "instagram", "Dd6ZIeCEzmh", "https://www.instagram.com/reel/Dd6ZIeCEzmh/"),
    ("instagram.com/p/C1a2B3c4D5e/", "instagram", "C1a2B3c4D5e", "https://www.instagram.com/p/C1a2B3c4D5e/"),
    ("https://www.instagram.com/someuser/reel/Dd6ZIeCEzmh/", "instagram", "Dd6ZIeCEzmh", "https://www.instagram.com/reel/Dd6ZIeCEzmh/"),
    ("https://www.tiktok.com/@creator/video/7312345678901234567?lang=en", "tiktok", "7312345678901234567", "https://www.tiktok.com/@creator/video/7312345678901234567"),
    ("https://vm.tiktok.com/ZMabc123/", "tiktok", "s-ZMabc123", "https://vm.tiktok.com/ZMabc123/"),
    ("https://youtube.com/shorts/abcDEF12345?feature=share", "youtube", "abcDEF12345", "https://www.youtube.com/shorts/abcDEF12345"),
    ("https://youtu.be/abcDEF12345", "youtube", "abcDEF12345", "https://www.youtube.com/shorts/abcDEF12345"),
    ("https://www.youtube.com/watch?v=abcDEF12345&t=3", "youtube", "abcDEF12345", "https://www.youtube.com/shorts/abcDEF12345"),
    ("https://twitter.com/someone/status/1790000000000000000?s=20", "x", "1790000000000000000", "https://x.com/someone/status/1790000000000000000"),
])
def test_parse(url, platform, cid, canonical):
    p = parse(url)
    assert (p.platform, p.content_id, p.url) == (platform, cid, canonical)


def test_same_post_same_cache_key():
    a = parse("https://www.instagram.com/reel/Dd6ZIeCEzmh")
    b = parse("https://instagram.com/reels/Dd6ZIeCEzmh/?igsh=xyz")
    c = parse("https://www.instagram.com/reel/OTHER123/")
    assert a.cache_key == b.cache_key != c.cache_key


@pytest.mark.parametrize("url", [
    "https://example.com/video/1",
    "https://www.instagram.com/someuser/",
    "https://www.tiktok.com/@creator",
    "https://x.com/someone",
    "not a url",
    "",
])
def test_unsupported(url):
    with pytest.raises(ReelError) as e:
        parse(url)
    assert e.value.code == "unsupported_url"


def test_find_url_in_message():
    assert find_url("check this out https://www.instagram.com/reel/Dd6ZIeCEzmh/. wild") == \
        "https://www.instagram.com/reel/Dd6ZIeCEzmh/"
    assert find_url("/force https://youtu.be/abcDEF12345") == "https://youtu.be/abcDEF12345"
    assert find_url("no link here") is None
