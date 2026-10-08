import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from instareader.config import Settings  # noqa: E402


class FakeMessages:
    """Stands in for AsyncAnthropic().messages: answers each forced tool call with canned input."""

    def __init__(self):
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        tool = kw["tools"][0]["name"]
        if tool == "record_frames":
            n = sum(1 for b in kw["messages"][0]["content"] if b.get("type") == "image")
            data = {"frames": [
                {"index": i + 1, "onscreen_text": "STEP 1: install" if i < 2 else "", "visual": f"frame {i + 1}"}
                for i in range(n)
            ]}
        else:
            data = {
                "title_guess": "Test reel", "creator": "", "summary": ["a", "b"], "claims": ["c"],
                "tools_or_products_mentioned": ["ToolX"], "links_mentioned": ["link in bio"],
                "action_items": ["try ToolX"],
            }
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name=tool, input=data)])


@pytest.fixture
def fake_anthropic():
    return SimpleNamespace(messages=FakeMessages())


@pytest.fixture
def settings(tmp_path):
    return Settings(anthropic_api_key="test", data_dir=tmp_path, http_token="secret")


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    """9s clip: three solid-colour 3s scenes (black, white, blue) with a tone, so scene detection has something to find."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run([
        "ffmpeg", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=320x568:d=3:r=25",
        "-f", "lavfi", "-i", "color=c=white:s=320x568:d=3:r=25",
        "-f", "lavfi", "-i", "color=c=blue:s=320x568:d=3:r=25",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=9",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(out),
    ], check=True)
    return out
