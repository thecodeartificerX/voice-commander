# Streaming Audio Pipeline: Real-Time VAD + Transcription

## Executive Summary

For a continuous Windows WASAPI daemon capturing 48 kHz audio, resampling to 16 kHz for Silero VAD, and transcribing utterances:

- **Resampler:** `soxr.ResampleStream` (best for real-time; lowest latency, highest quality, Windows-optimized)
- **Faster-Whisper input:** WAV file path (not numpy array; numpy arrays produce garbled output)
- **Threading topology:** PortAudio callback → queue → VAD worker thread → utterance queue → transcriber thread

---

## 1. Resampler Comparison & Choice

### Options Evaluated

| Resampler | Quality | Latency | Memory | Windows | Streaming? | Notes |
|-----------|---------|---------|--------|---------|----------|-------|
| **soxr.ResampleStream** | VHQ, HQ, MQ, LQ, QQ configurable | 0–15ms buffering | Low | Native | ✓ Yes | **BEST CHOICE** |
| scipy.signal.resample_poly | Good (sinc) | Low | Low | Native | ~ Batch only | Stateless; requires state mgmt |
| librosa.resample | Good (configurable) | ~50–100ms | Medium | Native | ~ Batch only | Built on resampy; overkill |

### **Recommendation: `soxr.ResampleStream`**

**Why:**
1. **True streaming API:** Maintains internal resampling state across chunk boundaries—you feed frames, it outputs proportionally (48→16 kHz = 1:3 ratio)
2. **Configurable quality:** QQ/LQ for speed in voice (QQ ≈ sub-1ms on CPU), HQ/VHQ if audio fidelity matters
3. **Zero jitter:** Handles non-aligned chunk sizes (e.g., PortAudio gives 2048 @ 48kHz; you feed, soxr buffers internally)
4. **Windows optimized:** Wraps libsoxr; pre-built wheels on PyPI; no system dependencies

**Installation:**
```bash
uv pip install soxr
```

**Streaming usage pattern:**
```python
import soxr
import numpy as np

resampler = soxr.ResampleStream(
    soxr_in_rate=48000,
    soxr_out_rate=16000,
    num_channels=1,
    dtype=np.float32,
    quality=soxr.HQ  # or soxr.LQ for speed
)

# In callback: feed 48 kHz frames, pull 16 kHz output
chunk_48k = indata.copy()  # shape (n_samples,) float32
output_16k = resampler.resample(chunk_48k)  # shape (n_samples * 16000/48000,)

# When done:
output_16k_final = resampler.flush()  # drain any remaining
```

**Why NOT scipy.signal.resample_poly:**
- Stateless; you'd have to manually track overlap state between chunks
- Polyphase is fast but requires integer ratios (48→16 = 3:1, OK here, but fragile if device rate varies)
- No built-in streaming constructor; requires manual windowing + overlap-add

**Why NOT librosa:**
- Heavier dependency (scipy + numba + resampy)
- Designed for batch (whole-file) resampling, not streaming loops
- Same underlying sinc as scipy; no advantage for real-time

---

## 2. Pre-Roll Buffer & Ring Buffer Design

Problem: Silero VAD triggers ~200 ms after speech onset. By then, the initial "p" sound of "paste" is lost.

**Solution:** Ring buffer keeps 200–300 ms of audio **before** VAD fires, then "retroactively" prepends it to the detected utterance.

### Ring Buffer Implementation

```python
from collections import deque
import threading

class RingBuffer:
    """Thread-safe ring buffer for pre-roll audio (float32, 16 kHz)."""
    
    def __init__(self, duration_sec: float, sample_rate: int = 16000):
        """
        Args:
            duration_sec: Buffer duration (e.g., 0.3 for 300 ms)
            sample_rate: Audio sample rate (16000 for VAD)
        """
        self.max_samples = int(duration_sec * sample_rate)
        self.buffer = deque(maxlen=self.max_samples)
        self.lock = threading.Lock()
    
    def push(self, frame: np.ndarray):
        """Add audio frame (mono float32, any length)."""
        with self.lock:
            self.buffer.extend(frame)
    
    def get_all(self) -> np.ndarray:
        """Return all buffered audio as concatenated array."""
        with self.lock:
            return np.concatenate(list(self.buffer)) if self.buffer else np.array([], dtype=np.float32)
    
    def clear(self):
        """Empty the buffer."""
        with self.lock:
            self.buffer.clear()
```

### Chunk-Size Reconciliation

- **PortAudio InputStream callback:** Fires every N samples at device native rate (48 kHz). N depends on device; typically 256–2048.
  - Example: 48 kHz @ 512 samples = 10.67 ms per callback
  
- **Silero VAD:** Wants exactly 512 samples @ 16 kHz = 32 ms per frame. State is maintained; can be fed any size, but must align for reliability.

- **Reconciliation strategy:**
  ```
  PortAudio callback (48 kHz)
        ↓
   Resample 48→16 kHz (with state)
        ↓
   Accumulate to VAD window (512 samples @ 16 kHz)
        ↓
   Feed to VAD when 512 samples ready
        ↓
   Push resampled chunk to ring buffer ALWAYS (pre-roll)
  ```

**Pseudocode:**
```python
class StreamPipeline:
    def __init__(self):
        self.resampler = soxr.ResampleStream(48000, 16000, 1, np.float32)
        self.vad = OnnxModelContainer(
            torch.jit.load('silero_vad.jit'),
            force_onnx=True
        )
        self.ring_buf = RingBuffer(duration_sec=0.3, sample_rate=16000)
        self.vad_accumulator = deque()  # Accumulate 512-sample chunks
        self.utterance_queue = queue.Queue()  # Thread-safe queue to transcriber
    
    def on_audio_callback(self, indata, frames, time, status):
        """PortAudio callback. Runs on PortAudio thread (real-time constraint)."""
        if status:
            log.warning(f"Audio callback status: {status}")
        
        # Resample 48 kHz → 16 kHz
        audio_16k = self.resampler.resample(indata.flatten())
        
        # Always push to ring buffer (pre-roll)
        self.ring_buf.push(audio_16k)
        
        # Accumulate VAD chunks (512 samples)
        self.vad_accumulator.extend(audio_16k)
        
        # When 512 samples ready, signal VAD worker
        if len(self.vad_accumulator) >= 512:
            chunk = np.array(list(self.vad_accumulator))[:512]
            self.vad_accumulator = deque(list(self.vad_accumulator)[512:])
            
            # DO NOT RUN VAD HERE—post to queue
            self.vad_input_queue.put(chunk)
    
    def vad_worker(self):
        """Runs on separate thread. Pulls VAD chunks, detects speech, manages utterances."""
        utterance_buffer = deque()
        speech_started = False
        
        while True:
            chunk = self.vad_input_queue.get()
            
            # Run VAD (cheap—<1ms on CPU)
            conf = self.vad(chunk, sr=16000)  # Returns confidence 0–1
            is_speech = conf > 0.5
            
            if is_speech and not speech_started:
                # Speech onset detected
                speech_started = True
                pre_roll = self.ring_buf.get_all()
                utterance_buffer.extend(pre_roll)
                log.info(f"Speech start detected. Pre-roll: {len(pre_roll)} samples")
                self.ring_buf.clear()
            
            if speech_started:
                utterance_buffer.extend(chunk)
            
            if not is_speech and speech_started:
                # Speech end detected (minimum utterance length check)
                utterance = np.array(list(utterance_buffer), dtype=np.float32)
                if len(utterance) > 16000 * 0.5:  # ≥ 500 ms
                    self.utterance_queue.put(utterance)
                    log.info(f"Utterance captured: {len(utterance)} samples")
                
                utterance_buffer.clear()
                speech_started = False
```

---

## 3. Handling Xruns & Callback Status Flags

### Status Flags (sounddevice.CallbackFlags)

```python
sd.CallbackFlags:
  .input_overflow   # Dropout on input (speech missed)
  .input_underflow  # Buffer starvation (shouldn't happen on input)
  .output_overflow  # (N/A for InputStream)
  .output_underflow # (N/A for InputStream)
  .priming_output   # (N/A for InputStream)
```

### Strategy

```python
def on_audio_callback(self, indata, frames, time, status):
    if status.input_overflow:
        log.error("INPUT OVERFLOW: audio may have been lost")
        self.stats['xruns'] += 1
        # Optionally: self.ring_buf.clear() to resync
    
    if status.input_underflow:
        log.warning("Input underflow (rare on recording)")
```

**When xrun occurs:** Audio is dropped by the OS; you miss speech. Silero VAD will then falsely report non-speech (silence). Consider:
- Increasing device buffer (sounddevice doesn't expose this directly; set in Windows audio settings)
- Reducing VAD processing latency (use soxr QQ instead of HQ)
- Logging xruns to measure system stability

---

## 4. Thread-Safety: Callback → Queue → VAD → Transcriber

### Threading Topology

```
┌─ PortAudio Driver
│  └─ InputStream callback (RT thread, 10–30 ms per invocation)
│     ├─ Resample 48→16 kHz
│     └─ Push VAD chunks to input_queue
│
├─ VAD Worker Thread (normal priority)
│  └─ Pull VAD chunks from input_queue
│     ├─ Run VAD (< 1 ms, CPU-bound)
│     └─ On utterance complete: write to WAV, push path to transcribe_queue
│
└─ Transcriber Thread (normal priority)
   └─ Pull WAV paths from transcribe_queue
      ├─ Run faster-whisper
      └─ Log result, push to dispatcher
```

### Thread Safety Rules

1. **PortAudio callback must NOT block** (< 1 ms execution). Use `queue.Queue` (thread-safe FIFO):
   ```python
   self.vad_input_queue = queue.Queue(maxsize=10)  # Bounded; blocks pusher if full
   # In callback: self.vad_input_queue.put(chunk, block=False) on overflow risk
   ```

2. **Silero VAD model is NOT thread-safe.** Keep VAD in its own worker thread:
   ```python
   # Load model once per thread
   class VADWorker(threading.Thread):
       def __init__(self, input_q, output_q):
           super().__init__(daemon=True)
           self.input_q = input_q
           self.output_q = output_q
           self.model = OnnxModelContainer(...)  # Load here, not in main
       
       def run(self):
           while True:
               chunk = self.input_q.get()
               conf = self.model(chunk, sr=16000)  # Safe—single thread owns model
               self.output_q.put(conf)
   ```

3. **Ring buffer:** Protected by lock (see section 2).

4. **utterance → WAV:** Use temporary files (not in-memory) so transcriber can read independently.

---

## 5. Utterance → WAV: Temporary File Path (Not Numpy Array)

### Why NOT numpy array for faster-whisper:

- **Finding:** faster-whisper `transcribe()` expects a **file path** (string) or file-like object.
- **Issue with numpy arrays:** Users report [garbled/gibberish output](https://github.com/SYSTRAN/faster-whisper/issues/1323) when attempting to pass arrays directly.
- **Root cause:** faster-whisper uses `ffmpeg` (via PyAV) to decode audio; if you bypass that with a raw array, format metadata is lost (sample rate, bit depth, endianness).

### Solution: Write temporary WAV per utterance

```python
import tempfile
import soundfile as sf

class VADWorker:
    # ...
    
    def save_utterance_wav(self, audio_16k: np.ndarray) -> str:
        """
        Save 16 kHz float32 mono audio to temporary WAV.
        Returns: Path to WAV file (caller responsible for cleanup).
        """
        fd, wav_path = tempfile.mkstemp(suffix='.wav')
        os.close(fd)
        
        sf.write(wav_path, audio_16k, samplerate=16000, subtype='FLOAT')
        return wav_path
    
    def run(self):
        while True:
            # ... VAD loop ...
            if utterance_complete:
                utterance = np.array(list(utterance_buffer), dtype=np.float32)
                wav_path = self.save_utterance_wav(utterance)
                self.utterance_queue.put(wav_path)

class TranscriberWorker:
    def __init__(self, model_size='small.en'):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(model_size, device='cuda', compute_type='float16')
    
    def run(self):
        while True:
            wav_path = self.utterance_queue.get()
            try:
                segments, info = self.model.transcribe(wav_path)
                text = ''.join(seg.text for seg in segments)
                log.info(f"Transcribed: {text}")
            finally:
                os.remove(wav_path)  # Cleanup temp file
```

**Why this is better:**
- faster-whisper internally handles resampling (if input isn't 16 kHz, it resamples—redundant but safe)
- WAV format metadata is preserved (endianness, bit depth)
- If transcription fails, you have the WAV for debugging

---

## 6. Real-World OSS Reference Implementations

### whisper-live
[GitHub: openai-whisper/whisper-live](https://github.com/openai-whisper/whisper-live)
- Ring buffer for pre-roll (100–500 ms)
- VAD-driven (silero-vad or pyannote)
- Utterance → numpy array → WhisperModel.transcribe()
- **Note:** Some users report quality issues with array input; WAV path is safer

### whisper_streaming
[GitHub: ufal/whisper_streaming](https://github.com/ufal/whisper_streaming)
- Streaming sliding-window approach (30 ms steps)
- Real-time transcript output
- Uses librosa.resample (batch); richer than needed for VAD

### whispering-live
[GitHub: mlosicki/whispering-live](https://github.com/mlosicki/whispering-live)
- Continuous sounddevice InputStream
- Queue-based processing (similar topology to above)
- WAV temp file → transcription

---

## 7. Chunk-Size Math Summary

**Device:** 48 kHz, assume 512 samples per callback (PortAudio standard on WASAPI)
- **Callback frequency:** 512 / 48000 ≈ 10.67 ms

**Resampling:** 48 kHz → 16 kHz (ratio 1:3)
- **Input:** 512 @ 48 kHz
- **Output:** ~171 samples @ 16 kHz

**Silero VAD window:** 512 samples @ 16 kHz = 32 ms
- **Accumulate:** 512 samples / 171 per callback ≈ 3 callbacks ≈ 32 ms ✓

**Ring buffer:** 300 ms @ 16 kHz
- **Samples:** 300 × 16 = 4,800 samples (negligible memory)

---

## 8. Implementation Checklist

- [ ] Install `soxr` for ResampleStream
- [ ] Install `silero-vad` for VADIterator
- [ ] Install `soundfile` for WAV writing
- [ ] Implement RingBuffer (section 2)
- [ ] Create VADWorker thread with model loaded in `__init__`
- [ ] Create TranscriberWorker thread with faster-whisper model loaded
- [ ] Use queue.Queue for callback → VAD and VAD → transcriber
- [ ] Measure callback time (should be < 1 ms); log xruns
- [ ] Test end-to-end with 5–10 utterances; measure latency end-to-end
- [ ] Log ring-buffer prepend length to verify pre-roll is working

---

## References

- [python-sounddevice docs](https://python-sounddevice.readthedocs.io/)
- [soxr PyPI](https://pypi.org/project/soxr/)
- [Silero VAD GitHub](https://github.com/snakers4/silero-vad)
- [faster-whisper issue #1323 (numpy array input)](https://github.com/SYSTRAN/faster-whisper/issues/1323)
- [whisper-live streaming example](https://github.com/openai-whisper/whisper-live)
- [scipy.signal.resample_poly docs](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.resample_poly.html)
