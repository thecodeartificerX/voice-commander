import numpy as np
import pytest
import soundfile as sf

from voice_commander.recorder import Recorder


def test_start_then_stop_writes_wav(tmp_path, monkeypatch):
    # Patch query_devices to return a device with 48 kHz native rate.
    import sounddevice as sd

    monkeypatch.setattr(
        sd, "query_devices", lambda idx: {"default_samplerate": 48000.0, "name": "test"}
    )

    rec = Recorder(output_dir=tmp_path, channels=1)

    # Track the samplerate the stream was opened with.
    opened_samplerate = {}

    class _FakeStream:
        def __init__(self, **kwargs):
            opened_samplerate["samplerate"] = kwargs.get("samplerate")

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sd, "InputStream", _FakeStream)

    rec.start()
    assert rec.actual_sample_rate == 48000
    assert opened_samplerate["samplerate"] == 48000

    # Feed 0.5 s of silence at 48 kHz.
    frames = np.zeros((24000, 1), dtype=np.float32)
    rec._on_audio(frames, 24000, None, None)  # type: ignore[arg-type]
    out = rec.stop()
    assert out == tmp_path / "recorded.wav"
    assert out.exists()
    info = sf.info(str(out))
    assert info.samplerate == 48000
    assert info.frames == 24000

    # Second record/stop cycle: same path returned, still only one WAV file.
    rec.start()
    rec._on_audio(frames, 24000, None, None)  # type: ignore[arg-type]
    out2 = rec.stop()
    assert out2 == tmp_path / "recorded.wav"
    assert len(list(tmp_path.glob("*.wav"))) == 1


def test_stop_without_start_raises(tmp_path):
    rec = Recorder(output_dir=tmp_path)
    with pytest.raises(RuntimeError):
        rec.stop()
