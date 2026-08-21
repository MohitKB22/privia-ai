"""PRIVIA speech pipeline.

    microphone -> WAV -> VAD -> speech-to-text -> normalisation -> agent
                                                              -> text -> TTS

Every stage runs on this machine. Audio is never written to disk by the server
and never leaves the device.
"""

from __future__ import annotations

from privia_shared.config import Settings

from .audio import (
    MAX_AUDIO_SECONDS,
    TARGET_SAMPLE_RATE,
    AudioClip,
    AudioError,
    EnergyVad,
    VadResult,
    decode_wav,
)
from .stt import (
    DisabledStt,
    FasterWhisperStt,
    SttProvider,
    Transcription,
    TranscriptionSegment,
)
from .tts import MAX_SPEAK_CHARS, DisabledTts, Pyttsx3Tts, TtsProvider

__all__ = [
    "MAX_AUDIO_SECONDS",
    "MAX_SPEAK_CHARS",
    "TARGET_SAMPLE_RATE",
    "AudioClip",
    "AudioError",
    "DisabledStt",
    "DisabledTts",
    "EnergyVad",
    "FasterWhisperStt",
    "Pyttsx3Tts",
    "SttProvider",
    "Transcription",
    "TranscriptionSegment",
    "TtsProvider",
    "VadResult",
    "build_stt",
    "build_tts",
    "decode_wav",
    "normalise_transcript",
]


def build_stt(settings: Settings) -> SttProvider:
    if settings.stt_provider == "disabled":
        return DisabledStt()
    return FasterWhisperStt(
        settings.stt_model,
        compute_type=settings.stt_compute_type,
        download_root=str(settings.data_dir / "models"),
    )


def build_tts(settings: Settings) -> TtsProvider:
    if settings.tts_provider == "pyttsx3":
        return Pyttsx3Tts()
    return DisabledTts()


def normalise_transcript(text: str) -> str:
    """Tidy a raw transcript before it reaches the agent.

    Speech models emit filler and inconsistent casing; the intent classifier is
    sensitive to both. This is cleanup only - no meaning is changed.
    """
    import re

    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    # Remove the filler and any punctuation that was attached to it, otherwise
    # stripping "um," leaves a stray comma at the start of the sentence.
    cleaned = re.sub(
        r"(?:(?<=^)|(?<=[\s,.!?]))(?:um+|uh+|erm+|hmm+|you know|i mean|sort of|kind of)"
        r"\s*[,.]?\s*",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+([,.!?])", r"\1", cleaned)
    cleaned = re.sub(r"([,.!?])\1+", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = cleaned.lstrip(" ,.;:-").strip()
    cleaned = re.sub(r"[,\s]+$", "", cleaned)
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned
