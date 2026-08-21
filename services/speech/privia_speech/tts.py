"""Text to speech.

Optional and off by default. PRIVIA never speaks unless the user asks it to, and
never records unless push-to-talk is held.
"""

from __future__ import annotations

import abc
import asyncio
import tempfile
from pathlib import Path

from privia_shared.domain import IntegrationInfo
from privia_shared.enums import IntegrationStatus
from privia_shared.errors import TtsUnavailableError
from privia_shared.ids import utcnow

MAX_SPEAK_CHARS = 4000


class TtsProvider(abc.ABC):
    name = "tts"

    @abc.abstractmethod
    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        """Return WAV bytes."""

    @abc.abstractmethod
    async def health_check(self) -> IntegrationInfo: ...

    async def voices(self) -> list[str]:
        return []


class Pyttsx3Tts(TtsProvider):
    """Offline synthesis through the operating system's own voices."""

    name = "pyttsx3"

    def __init__(self, rate: int = 185) -> None:
        self.rate = rate

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        cleaned = (text or "").strip()[:MAX_SPEAK_CHARS]
        if not cleaned:
            raise TtsUnavailableError("There is nothing to say.")
        return await asyncio.to_thread(self._synthesize_sync, cleaned, voice)

    def _synthesize_sync(self, text: str, voice: str | None) -> bytes:
        try:
            import pyttsx3
        except ImportError as exc:
            raise TtsUnavailableError(
                "pyttsx3 is not installed. Install it with: pip install 'privia[speech]'"
            ) from exc
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", self.rate)
            if voice:
                for candidate in engine.getProperty("voices"):
                    if voice.lower() in (candidate.name or "").lower():
                        engine.setProperty("voice", candidate.id)
                        break
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "speech.wav"
                engine.save_to_file(text, str(target))
                engine.runAndWait()
                engine.stop()
                if not target.exists():
                    raise TtsUnavailableError(
                        "The system speech engine produced no audio on this machine."
                    )
                return target.read_bytes()
        except TtsUnavailableError:
            raise
        except Exception as exc:
            raise TtsUnavailableError(
                f"The system speech engine failed ({type(exc).__name__})."
            ) from exc

    async def health_check(self) -> IntegrationInfo:
        try:
            import pyttsx3  # noqa: F401
        except ImportError:
            return IntegrationInfo(
                name="speech.tts",
                family="speech",
                provider=self.name,
                status=IntegrationStatus.NOT_CONFIGURED,
                detail="pyttsx3 is not installed. Install with: pip install 'privia[speech]'",
                checked_at=utcnow(),
            )
        return IntegrationInfo(
            name="speech.tts",
            family="speech",
            provider=self.name,
            status=IntegrationStatus.READY,
            capabilities=("synthesize", "local-only"),
            detail="Uses the operating system's built-in voices",
            checked_at=utcnow(),
        )


class DisabledTts(TtsProvider):
    name = "disabled"

    def __init__(self, reason: str = "Spoken replies are switched off in Settings.") -> None:
        self.reason = reason

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        raise TtsUnavailableError(self.reason)

    async def health_check(self) -> IntegrationInfo:
        return IntegrationInfo(
            name="speech.tts",
            family="speech",
            provider=self.name,
            status=IntegrationStatus.NOT_CONFIGURED,
            detail=self.reason,
            checked_at=utcnow(),
        )
