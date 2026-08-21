"""Voice endpoints.

Audio arrives, is decoded and checked for actual speech, is transcribed locally,
and is then discarded. The server never writes audio to disk.
"""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field

from privia_shared.errors import BadRequestError, PayloadTooLargeError
from privia_speech import AudioError, EnergyVad, decode_wav, normalise_transcript

from ..deps import ContainerDep, RequestIdDep

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    voice: str | None = None


@router.get("/status", summary="Speech availability")
async def status(container: ContainerDep) -> dict[str, Any]:
    stt = await container.stt.health_check()
    tts = await container.tts.health_check()
    return {
        "stt": stt.model_dump(mode="json"),
        "tts": tts.model_dump(mode="json"),
        "accepted_format": "16-bit PCM WAV, any sample rate (resampled to 16 kHz mono)",
        "max_seconds": 300,
        "recording_policy": (
            "Audio is only captured while push-to-talk is held, is processed in memory, "
            "and is never written to disk or transmitted."
        ),
    }


@router.post("/transcribe", summary="Transcribe speech to text")
async def transcribe(
    container: ContainerDep,
    request_id: RequestIdDep,
    audio: UploadFile = File(..., description="16-bit PCM WAV"),
    language: str | None = Form(default=None),
) -> dict[str, Any]:
    payload = await audio.read()
    if len(payload) > container.settings.max_upload_bytes:
        raise PayloadTooLargeError(
            f"The clip exceeds the {container.settings.max_upload_bytes:,} byte limit."
        )
    try:
        clip = decode_wav(payload)
    except AudioError as exc:
        raise BadRequestError(str(exc)) from exc

    vad = EnergyVad().analyse(clip)
    if not vad.speech_detected:
        return {
            "text": "",
            "speech_detected": False,
            "duration_seconds": round(clip.duration_seconds, 2),
            "message": "I did not hear anything. Check the microphone input level.",
            "vad": vad.__dict__,
        }

    transcription = await container.stt.transcribe(clip, language=language)
    text = normalise_transcript(transcription.text)
    container.metrics.observe("stt.latency", transcription.latency_ms)
    container.logger.info(
        "voice.transcribed",
        duration_seconds=round(clip.duration_seconds, 2),
        latency_ms=transcription.latency_ms,
        characters=len(text),
        request_id=request_id,
    )
    return {
        "text": text,
        "raw_text": transcription.text,
        "language": transcription.language,
        "speech_detected": True,
        "duration_seconds": round(clip.duration_seconds, 2),
        "latency_ms": transcription.latency_ms,
        "model": transcription.model,
        "confidence": transcription.confidence,
        "segments": [s.__dict__ for s in transcription.segments],
        "processed": "locally",
    }


@router.post("/synthesize", summary="Speak text")
async def synthesize(
    body: SynthesizeRequest, container: ContainerDep, request_id: RequestIdDep
) -> dict[str, Any]:
    audio = await container.tts.synthesize(body.text, voice=body.voice)
    return {
        "audio_base64": base64.b64encode(audio).decode("ascii"),
        "format": "wav",
        "bytes": len(audio),
        "processed": "locally",
    }
