"""Audio decoding and voice activity detection, standard library only.

PRIVIA accepts WAV directly. Other container formats are rejected with a clear
message rather than silently mis-decoded, because a wrong sample rate produces
plausible-sounding nonsense from a speech model, which is worse than an error.
"""

from __future__ import annotations

import audioop
import io
import math
import wave
from dataclasses import dataclass

TARGET_SAMPLE_RATE = 16_000
MAX_AUDIO_SECONDS = 300


@dataclass
class AudioClip:
    """16-bit mono PCM at a known sample rate."""

    samples: bytes
    sample_rate: int
    channels: int = 1
    sample_width: int = 2

    @property
    def duration_seconds(self) -> float:
        frames = len(self.samples) / (self.sample_width * self.channels)
        return frames / self.sample_rate if self.sample_rate else 0.0

    @property
    def frame_count(self) -> int:
        return len(self.samples) // (self.sample_width * self.channels)

    def to_float32(self) -> list[float]:
        """Normalised samples, for models that want floats."""
        count = len(self.samples) // 2
        if count == 0:
            return []
        import struct

        raw = struct.unpack(f"<{count}h", self.samples[: count * 2])
        return [value / 32768.0 for value in raw]

    def to_wav_bytes(self) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(self.channels)
            handle.setsampwidth(self.sample_width)
            handle.setframerate(self.sample_rate)
            handle.writeframes(self.samples)
        return buffer.getvalue()


class AudioError(ValueError):
    """Raised for audio PRIVIA cannot safely decode."""


def decode_wav(data: bytes, *, target_rate: int = TARGET_SAMPLE_RATE) -> AudioClip:
    """Decode WAV bytes to 16 kHz mono 16-bit PCM."""
    if not data:
        raise AudioError("The audio payload is empty.")
    if data[:4] != b"RIFF":
        raise AudioError(
            "Only WAV audio is accepted. Send 16-bit PCM WAV; the desktop client records "
            "in that format."
        )
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.getnframes()
            if rate <= 0:
                raise AudioError("The WAV header declares an invalid sample rate.")
            if frames / rate > MAX_AUDIO_SECONDS:
                raise AudioError(
                    f"The clip is longer than {MAX_AUDIO_SECONDS} seconds. Record in shorter turns."
                )
            payload = handle.readframes(frames)
    except wave.Error as exc:
        raise AudioError(f"The WAV file could not be read: {exc}") from exc

    if width != 2:
        payload = audioop.lin2lin(payload, width, 2)
        width = 2
    if channels > 1:
        payload = audioop.tomono(payload, width, 0.5, 0.5)
        channels = 1
    if rate != target_rate:
        payload, _state = audioop.ratecv(payload, width, channels, rate, target_rate, None)
        rate = target_rate
    return AudioClip(samples=payload, sample_rate=rate, channels=channels, sample_width=width)


@dataclass
class VadResult:
    speech_detected: bool
    speech_ratio: float
    peak_dbfs: float
    leading_silence_ms: int
    trailing_silence_ms: int


class EnergyVad:
    """Frame-energy voice activity detection.

    Deliberately simple: its job is to answer "did the microphone actually pick
    anything up?" so PRIVIA can say "I did not hear anything" instead of sending
    silence to a speech model and inventing a transcript.
    """

    def __init__(
        self,
        *,
        frame_ms: int = 30,
        threshold_dbfs: float = -42.0,
        min_speech_ratio: float = 0.06,
    ) -> None:
        self.frame_ms = frame_ms
        self.threshold_dbfs = threshold_dbfs
        self.min_speech_ratio = min_speech_ratio

    def analyse(self, clip: AudioClip) -> VadResult:
        frame_bytes = int(clip.sample_rate * self.frame_ms / 1000) * clip.sample_width
        if frame_bytes <= 0 or not clip.samples:
            return VadResult(False, 0.0, -120.0, 0, 0)
        flags: list[bool] = []
        peak = 0
        for offset in range(0, len(clip.samples) - frame_bytes + 1, frame_bytes):
            frame = clip.samples[offset : offset + frame_bytes]
            rms = audioop.rms(frame, clip.sample_width)
            peak = max(peak, audioop.max(frame, clip.sample_width))
            flags.append(_dbfs(rms) > self.threshold_dbfs)
        if not flags:
            return VadResult(False, 0.0, _dbfs(peak), 0, 0)
        ratio = sum(flags) / len(flags)
        leading = _run_length(flags, False)
        trailing = _run_length(list(reversed(flags)), False)
        return VadResult(
            speech_detected=ratio >= self.min_speech_ratio,
            speech_ratio=round(ratio, 4),
            peak_dbfs=round(_dbfs(peak), 1),
            leading_silence_ms=leading * self.frame_ms,
            trailing_silence_ms=trailing * self.frame_ms,
        )

    def trim(self, clip: AudioClip, *, padding_ms: int = 120) -> AudioClip:
        """Drop leading and trailing silence, keeping a little padding."""
        result = self.analyse(clip)
        if not result.speech_detected:
            return clip
        bytes_per_ms = int(clip.sample_rate * clip.sample_width / 1000)
        start = max(0, (result.leading_silence_ms - padding_ms) * bytes_per_ms)
        end = len(clip.samples) - max(0, (result.trailing_silence_ms - padding_ms) * bytes_per_ms)
        if end <= start:
            return clip
        return AudioClip(
            samples=clip.samples[start:end],
            sample_rate=clip.sample_rate,
            channels=clip.channels,
            sample_width=clip.sample_width,
        )


def _dbfs(value: float) -> float:
    if value <= 0:
        return -120.0
    return 20 * math.log10(min(value, 32767) / 32767.0)


def _run_length(flags: list[bool], value: bool) -> int:
    count = 0
    for flag in flags:
        if flag != value:
            break
        count += 1
    return count
