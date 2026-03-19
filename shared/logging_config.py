"""Structured logging setup for Idea Roast (console + rotating file)."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

_THIRD_PARTY_LOGGERS = (
    "httpx",
    "httpcore",
    "telegram",
    "telegram.ext",
    "anthropic",
    "openai",
)

_OUR_PREFIXES = ("bot", "modules", "tools", "shared", "llm", "db")


def _resolve_log_path() -> Path:
    """Docker: /app/data/idearoast.log; lokal: ./data/idearoast.log."""
    if Path("/app").is_dir():
        root = Path("/app/data")
    else:
        root = Path("data")
    root.mkdir(parents=True, exist_ok=True)
    return root / "idearoast.log"


def setup_logging(log_level: str = "INFO") -> None:
    """Configure root logger: stdout + rotating file, third-party noise reduced."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    formatter = logging.Formatter(_FORMAT)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    log_path = _resolve_log_path()
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    for prefix in _OUR_PREFIXES:
        logging.getLogger(prefix).setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given name (same as logging.getLogger)."""
    return logging.getLogger(name)
