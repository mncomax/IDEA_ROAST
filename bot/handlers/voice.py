"""Telegram voice messages: OGG → WAV → Whisper → same routing as text."""

from __future__ import annotations

import asyncio
import logging

import ffmpeg
from telegram import Update
from telegram.ext import ContextTypes

from bot.config import get_settings
from shared.exceptions import VoiceTranscriptionError
from tools.whisper import WhisperClient

from . import dispatch_transcribed_text

logger = logging.getLogger(__name__)


def _ogg_to_wav_bytes(ogg_bytes: bytes) -> bytes:
    out, _err = (
        ffmpeg.input("pipe:")
        .output("pipe:", format="wav", acodec="pcm_s16le", ac=1, ar=16000)
        .run(
            input=ogg_bytes,
            capture_stdout=True,
            capture_stderr=True,
        )
    )
    return out


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.voice:
        return

    await update.message.reply_text("🎙️ Verarbeite Sprachnachricht...")

    try:
        tg_file = await update.message.voice.get_file()
        ogg_buf = await tg_file.download_as_bytearray()
        ogg_bytes = bytes(ogg_buf)

        wav_bytes = await asyncio.to_thread(_ogg_to_wav_bytes, ogg_bytes)

        settings = context.bot_data.get("settings")
        if settings is None:
            settings = get_settings()
        whisper = WhisperClient(base_url=settings.whisper_base_url)
        transcription = await whisper.transcribe(wav_bytes, filename="voice.wav")

    except VoiceTranscriptionError as exc:
        logger.warning("Voice transcription failed: %s", exc)
        await update.message.reply_text(
            "Sprache konnte nicht erkannt werden. Bitte versuche es noch einmal "
            "oder schreib deine Idee als Text."
        )
        return
    except ffmpeg.Error as exc:
        err_hint = ""
        if getattr(exc, "stderr", None):
            err_hint = exc.stderr.decode("utf-8", errors="replace")[:300]
        logger.warning("ffmpeg voice conversion failed: %s", err_hint or exc)
        await update.message.reply_text(
            "Die Sprachnachricht konnte nicht verarbeitet werden. "
            "Bitte versuche eine andere Aufnahme oder nutze Text."
        )
        return
    except Exception:
        logger.exception("Unexpected error in handle_voice")
        await update.message.reply_text(
            "Etwas ist schiefgelaufen. Bitte versuche es spaeter erneut."
        )
        return

    stripped = transcription.strip()
    if not stripped:
        await update.message.reply_text(
            "Ich habe nichts verstanden — bitte nochmal sprechen."
        )
        return

    preview = stripped if len(stripped) <= 200 else stripped[:200] + "..."
    await update.message.reply_text(f"🎤 Verstanden: {preview}")

    await dispatch_transcribed_text(update, context, stripped)
