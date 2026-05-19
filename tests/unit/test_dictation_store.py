import wave

import numpy as np

from voice_commander.dictation.store import DictationStore, encode_wav


def test_encode_wav_is_16k_mono_s16le():
    audio = np.zeros(16000, dtype=np.float32)
    data = encode_wav(audio)
    with wave.open(__import__("io").BytesIO(data), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.getnframes() == 16000


def test_encode_wav_clips_and_scales():
    audio = np.array([1.5, -1.5, 0.0], dtype=np.float32)
    data = encode_wav(audio)
    with wave.open(__import__("io").BytesIO(data), "rb") as w:
        frames = np.frombuffer(w.readframes(3), dtype="<i2")
    assert frames[0] == 32767
    assert frames[1] == -32767
    assert frames[2] == 0


def test_store_text_roundtrip(tmp_path):
    store = DictationStore(tmp_path / "dictation")
    assert store.read_text() is None
    store.save_text("hello world")
    assert store.read_text() == "hello world"
    assert store.text_path.exists()


def test_store_save_overwrites(tmp_path):
    store = DictationStore(tmp_path / "dictation")
    store.save_text("first")
    store.save_text("second")
    assert store.read_text() == "second"
