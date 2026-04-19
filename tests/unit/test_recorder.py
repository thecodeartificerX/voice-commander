import numpy as np
import pytest
import soundfile as sf
from voice_commander.recorder import Recorder


def test_start_then_stop_writes_wav(tmp_path, monkeypatch):
    rec = Recorder(output_dir=tmp_path, sample_rate=16000, channels=1)
    # Patch out the real stream with a fake that we feed manually.
    rec._open_stream = lambda: _FakeStream(rec)
    rec.start()
    # Feed 0.5 s of silence frames.
    frames = np.zeros((8000, 1), dtype=np.float32)
    rec._on_audio(frames, 8000, None, None)  # type: ignore[arg-type]
    out = rec.stop()
    assert out == tmp_path / "recorded.wav"
    assert out.exists()
    data, sr = sf.read(out)
    assert sr == 16000
    assert data.shape[0] == 8000

    # Second record/stop cycle: same path returned, still only one WAV file.
    rec._open_stream = lambda: _FakeStream(rec)
    rec.start()
    rec._on_audio(frames, 8000, None, None)  # type: ignore[arg-type]
    out2 = rec.stop()
    assert out2 == tmp_path / "recorded.wav"
    assert len(list(tmp_path.glob("*.wav"))) == 1


def test_stop_without_start_raises(tmp_path):
    rec = Recorder(output_dir=tmp_path)
    with pytest.raises(RuntimeError):
        rec.stop()


class _FakeStream:
    def __init__(self, rec): self.rec = rec
    def start(self): pass
    def stop(self): pass
    def close(self): pass
