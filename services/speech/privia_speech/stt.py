"""Speech to text.

``faster-whisper`` is an optional dependency. When it is missing or a model has
not been downloaded, PRIVIA says so in plain language and the user keeps typing;
it never guesses at a transcript.
"""

from __future__ import annotations

import abc
import asyncio
import time
from dataclasses import dataclass, field

from privia_shared.domain import IntegrationInfo
from privia_shared.enums import IntegrationStatus
from privia_shared.errors import SttUnavailableError
from privia_shared.ids import utcnow

from .audio import AudioClip, EnergyVad, decode_wav


@dataclass
class TranscriptionSegment:
    start: float
    end: float
    text: str


@dataclass
class Transcription:
    text: str
    language: str = "en"
    duration_seconds: float = 0.0
    latency_ms: int = 0
    model: str = ""
    segments: list[TranscriptionSegment] = field(default_factory=list)
    speech_detected: bool = True
    confidence: float | None = None


class SttProvider(abc.ABC):
    name = "stt"
    model = ""

    @abc.abstractmethod
    async def transcribe(
        self, clip: AudioClip, *, language: str | None = None
    ) -> Transcription: ...

    @abc.abstractmethod
    async def health_check(self) -> IntegrationInfo: ...

    async def close(self) -> None:
        return None


class FasterWhisperStt(SttProvider):
    """Local Whisper inference. Nothing is uploaded."""

    name = "faster-whisper"

    def __init__(
        self,
        model: str = "base.en",
        *,
        compute_type: str = "int8",
        device: str = "cpu",
        download_root: str | None = None,
    ) -> None:
        self.model = model
        self.compute_type = compute_type
        self.device = device
        self.download_root = download_root
        self._model: object | None = None
        self._load_error: str | None = None
        self._lock = asyncio.Lock()

    def _load(self) -> object:
        from faster_whisper import WhisperModel

        return WhisperModel(
            self.model,
            device=self.device,
            compute_type=self.compute_type,
            download_root=self.download_root,
        )

    async def _ensure_model(self) -> object:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is not None:
                return self._model
            try:
                self._model = await asyncio.to_thread(self._load)
            except ImportError as exc:
                self._load_error = (
                    "faster-whisper is not installed. Install it with: "
                    "pip install 'privia[speech]'"
                )
                raise SttUnavailableError(self._load_error) from exc
            except Exception as exc:
                self._load_error = (
                    f"The speech model '{self.model}' could not be loaded "
                    f"({type(exc).__name__}). You can keep typing."
                )
                raise SttUnavailableError(self._load_error) from exc
        return self._model

    async def transcribe(self, clip: AudioClip, *, language: str | None = None) -> Transcription:
        vad = EnergyVad().analyse(clip)
        if not vad.speech_detected:
            return Transcription(
                text="",
                duration_seconds=clip.duration_seconds,
                model=self.model,
                speech_detected=False,
            )
        model = await self._ensure_model()
        started = time.perf_counter()
        result = await asyncio.to_thread(self._transcribe_sync, model, clip, language)
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    def _transcribe_sync(
        self, model: object, clip: AudioClip, language: str | None
    ) -> Transcription:
        segments, info = model.transcribe(  # type: ignore[attr-defined]
            clip.to_float32(),
            language=language,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        collected: list[TranscriptionSegment] = []
        parts: list[str] = []
        for segment in segments:
            text = (segment.text or "").strip()
            if not text:
                continue
            collected.append(TranscriptionSegment(segment.start, segment.end, text))
            parts.append(text)
        return Transcription(
            text=" ".join(parts).strip(),
            language=getattr(info, "language", language or "en"),
            duration_seconds=clip.duration_seconds,
            model=self.model,
            segments=collected,
            confidence=getattr(info, "language_probability", None),
        )

    async def health_check(self) -> IntegrationInfo:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return IntegrationInfo(
                name="speech.stt",
                family="speech",
                provider=self.name,
                status=IntegrationStatus.NOT_CONFIGURED,
                detail="faster-whisper is not installed. Install with: pip install 'privia[speech]'",
                checked_at=utcnow(),
            )
        if self._load_error:
            return IntegrationInfo(
                name="speech.stt",
                family="speech",
                provider=self.name,
                status=IntegrationStatus.ERROR,
                detail=self._load_error,
                checked_at=utcnow(),
            )
        return IntegrationInfo(
            name="speech.stt",
            family="speech",
            provider=self.name,
            status=IntegrationStatus.READY,
            capabilities=("transcribe", "vad", "local-only"),
            detail=(
                f"Whisper '{self.model}' runs locally"
                + ("" if self._model is not None else "; the model loads on first use")
            ),
            checked_at=utcnow(),
        )


class DisabledStt(SttProvider):
    """Explicitly switched off. Fails clearly and immediately."""

    name = "disabled"

    def __init__(self, reason: str = "Speech to text is switched off in Settings.") -> None:
        self.reason = reason

    async def transcribe(self, clip: AudioClip, *, language: str | None = None) -> Transcription:
        raise SttUnavailableError(self.reason)

    async def health_check(self) -> IntegrationInfo:
        return IntegrationInfo(
            name="speech.stt",
            family="speech",
            provider=self.name,
            status=IntegrationStatus.NOT_CONFIGURED,
            detail=self.reason,
            checked_at=utcnow(),
        )


def transcribe_wav_bytes_sync_guard(data: bytes) -> AudioClip:
    """Decode and validate before any model touches the bytes."""
    return decode_wav(data)
