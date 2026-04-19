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
    assert out.exists()
    data, sr = sf.read(out)
    assert sr == 16000
    assert data.shape[0] == 8000


def test_stop_without_start_raises(tmp_path):
    rec = Recorder(output_dir=tmp_path)
    with pytest.raises(RuntimeError):
        rec.stop()


def test_retention_deletes_old_files(tmp_path):
    for i in range(5):
        (tmp_path / f"rec-{i:02d}.wav").write_bytes(b"x")
    rec = Recorder(output_dir=tmp_path, retention_count=2)
    rec.enforce_retention()
    remaining = sorted(p.name for p in tmp_path.glob("rec-*.wav"))
    assert len(remaining) == 2


class _FakeStream:
    def __init__(self, rec): self.rec = rec
    def start(self): pass
    def stop(self): pass
    def close(self): pass
