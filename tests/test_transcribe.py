import subprocess
import shutil
from types import SimpleNamespace

import numpy as np
import pytest

from instareader import transcribe
from instareader.transcribe import _local, load_wav


@pytest.fixture
def wav(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path / "a.wav"
    # same format media.extract_audio produces
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)], check=True)
    return out


def test_load_wav(wav):
    a = load_wav(wav)
    assert a.dtype == np.float32 and a.ndim == 1
    assert abs(len(a) - 32000) < 100 and 0.1 < np.abs(a).max() <= 1.0


def test_local_passes_samples_not_path(wav, settings, monkeypatch):
    seen = {}

    class FakeModel:
        def transcribe(self, audio, **kw):
            seen["audio"] = audio
            return iter([SimpleNamespace(text=" hello "), SimpleNamespace(text="world")]), SimpleNamespace(language="en")

    monkeypatch.setattr(transcribe, "_load_model", lambda s: FakeModel())
    tr = _local(wav, settings)
    assert isinstance(seen["audio"], np.ndarray)
    assert (tr.text, tr.language) == ("hello world", "en")
