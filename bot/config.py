"""Application settings loaded from environment variables (with optional .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _parse_allowed_user_ids(raw: str | None) -> list[int]:
    if not raw or not raw.strip():
        return []
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        ids.append(int(part))
    return ids


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    anthropic_api_key: str
    reddit_client_id: str | None
    reddit_client_secret: str | None
    reddit_user_agent: str | None
    github_token: str | None
    searxng_base_url: str
    whisper_base_url: str
    database_path: str
    log_level: str
    allowed_user_ids: list[int]


def load_settings() -> Settings:
    load_dotenv()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        msg = "TELEGRAM_BOT_TOKEN is required"
        raise ValueError(msg)

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        msg = "ANTHROPIC_API_KEY is required"
        raise ValueError(msg)

    allowed_raw = os.environ.get("ALLOWED_USER_IDS", "")

    return Settings(
        telegram_bot_token=token,
        anthropic_api_key=anthropic_key,
        reddit_client_id=os.environ.get("REDDIT_CLIENT_ID") or None,
        reddit_client_secret=os.environ.get("REDDIT_CLIENT_SECRET") or None,
        reddit_user_agent=os.environ.get("REDDIT_USER_AGENT") or None,
        github_token=os.environ.get("GITHUB_TOKEN") or None,
        searxng_base_url=os.environ.get("SEARXNG_BASE_URL", "http://searxng:8080"),
        whisper_base_url=os.environ.get("WHISPER_BASE_URL", "http://whisper:8000"),
        database_path=os.environ.get("DATABASE_PATH", "/app/data/idearoast.db"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        allowed_user_ids=_parse_allowed_user_ids(allowed_raw),
    )


def get_settings() -> Settings:
    """Return current application settings (backed by environment variables)."""
    return load_settings()
