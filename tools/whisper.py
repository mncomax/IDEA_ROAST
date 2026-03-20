"""Async client for faster-whisper-server (OpenAI-compatible transcriptions API)."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import aiohttp
from aiohttp import ClientTimeout

from shared.exceptions import VoiceTranscriptionError

DEFAULT_WHISPER_MODEL = "Systran/faster-whisper-medium"
TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


class WhisperClient:
    """POST audio to a faster-whisper-server instance."""

    def __init__(self, base_url: str | None = None) -> None:
        raw = base_url if base_url is not None else os.environ.get(
            "WHISPER_BASE_URL", "http://localhost:8000"
        )
        self._base_url = _normalize_base_url(raw)

    async def transcribe(self, audio_bytes: bytes, filename: str = "voice.wav") -> str:
        url = f"{self._base_url}{TRANSCRIPTIONS_PATH}"
        timeout = ClientTimeout(total=120)

        form = aiohttp.FormData()
        form.add_field(
            "file",
            audio_bytes,
            filename=filename,
            content_type="application/octet-stream",
        )
        form.add_field("model", DEFAULT_WHISPER_MODEL)
        form.add_field("language", "de")
        form.add_field("response_format", "json")

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=form) as resp:
                    body = await resp.read()
                    status = resp.status
        except asyncio.TimeoutError as exc:
            raise VoiceTranscriptionError(
                "Whisper-Anfrage hat das Zeitlimit (120s) ueberschritten."
            ) from exc
        except aiohttp.ClientError as exc:
            raise VoiceTranscriptionError(
                f"Whisper-Anfrage fehlgeschlagen (Netzwerk): {exc}"
            ) from exc

        if status != 200:
            detail = body.decode("utf-8", errors="replace")[:500]
            raise VoiceTranscriptionError(
                f"Whisper-Server antwortete mit HTTP {status}: {detail}"
            )

        try:
            payload: Any = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise VoiceTranscriptionError(
                "Whisper-Antwort war kein gueltiges JSON."
            ) from exc

        if isinstance(payload, dict) and "error" in payload:
            err = payload.get("error")
            msg = err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)
            raise VoiceTranscriptionError(f"Whisper API Fehler: {msg}")

        text: str | None = None
        if isinstance(payload, dict):
            raw_text = payload.get("text")
            if isinstance(raw_text, str):
                text = raw_text

        if not text:
            raise VoiceTranscriptionError(
                "Whisper-Antwort enthielt kein 'text'-Feld."
            )

        return text.strip()
