"""Handler für Bot-Statistiken (/stats)."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from shared.monitoring import format_system_status


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    text = await format_system_status()
    await update.message.reply_text(text)
