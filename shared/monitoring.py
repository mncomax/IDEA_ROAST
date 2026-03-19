"""In-memory bot metrics and helpers (no external services)."""

from __future__ import annotations

import asyncio
import sys
import time
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

_BOOT_MONO = time.monotonic()

_COUNTERS = (
    "ideas_created",
    "validations_run",
    "simulations_run",
    "llm_calls",
    "llm_errors",
    "research_cache_hits",
    "research_cache_misses",
    "voice_transcriptions",
)

_DURATION_CAPS = {
    "validation_duration_seconds": 20,
    "llm_call_duration_seconds": 50,
}

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


class BotMetrics:
    """Singleton: thread-safe counters and rolling duration samples (asyncio.Lock)."""

    _instance: Optional["BotMetrics"] = None

    def __new__(cls) -> "BotMetrics":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_state()
        return cls._instance

    def _init_state(self) -> None:
        self._lock = asyncio.Lock()
        self.ideas_created = 0
        self.validations_run = 0
        self.simulations_run = 0
        self.llm_calls = 0
        self.llm_errors = 0
        self.research_cache_hits = 0
        self.research_cache_misses = 0
        self.voice_transcriptions = 0
        self.validation_duration_seconds: List[float] = []
        self.llm_call_duration_seconds: List[float] = []

    async def increment(self, metric: str, amount: int = 1) -> None:
        if metric not in _COUNTERS or amount == 0:
            return
        async with self._lock:
            current = getattr(self, metric)
            setattr(self, metric, current + amount)

    async def record_duration(self, metric: str, seconds: float) -> None:
        if metric not in _DURATION_CAPS:
            return
        cap = _DURATION_CAPS[metric]
        async with self._lock:
            lst: List[float] = getattr(self, metric)
            lst.append(seconds)
            while len(lst) > cap:
                lst.pop(0)

    async def get_stats(self) -> Dict[str, Any]:
        async with self._lock:
            out: Dict[str, Any] = {}
            for name in _COUNTERS:
                out[name] = getattr(self, name)

            def _avg(samples: List[float]) -> Optional[float]:
                if not samples:
                    return None
                return sum(samples) / len(samples)

            out["validation_duration_avg_seconds"] = _avg(
                list(self.validation_duration_seconds)
            )
            out["llm_call_duration_avg_seconds"] = _avg(
                list(self.llm_call_duration_seconds)
            )
            out["validation_duration_samples"] = len(self.validation_duration_seconds)
            out["llm_call_duration_samples"] = len(self.llm_call_duration_seconds)
        return out

    async def format_stats_text(self) -> str:
        s = await self.get_stats()
        lines = [
            "📊 Metriken",
            f"Ideen: {s['ideas_created']}",
            f"Validierungen: {s['validations_run']}",
            f"Simulationen: {s['simulations_run']}",
            f"LLM-Aufrufe: {s['llm_calls']} (Fehler: {s['llm_errors']})",
            (
                "Research-Cache: "
                f"Treffer {s['research_cache_hits']}, "
                f"Miss {s['research_cache_misses']}"
            ),
            f"Sprach-Transkriptionen: {s['voice_transcriptions']}",
        ]
        v_avg = s["validation_duration_avg_seconds"]
        if v_avg is not None:
            lines.append(
                f"Ø Validierung: {v_avg:.2f}s "
                f"(n={s['validation_duration_samples']})"
            )
        else:
            lines.append("Ø Validierung: —")
        l_avg = s["llm_call_duration_avg_seconds"]
        if l_avg is not None:
            lines.append(
                f"Ø LLM-Aufruf: {l_avg:.2f}s "
                f"(n={s['llm_call_duration_samples']})"
            )
        else:
            lines.append("Ø LLM-Aufruf: —")
        return "\n".join(lines)


def _format_uptime(seconds: float) -> str:
    s = int(max(0, seconds))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts: List[str] = []
    if d:
        parts.append(f"{d}d")
    if h or d:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def track_duration(metric_name: str) -> Callable[[F], F]:
    """Async decorator: misst Laufzeit und ruft record_duration auf."""

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                await BotMetrics().record_duration(
                    metric_name, time.perf_counter() - t0
                )

        return wrapper  # type: ignore[return-value]

    return decorator


async def format_system_status() -> str:
    """Kompakter System- und Metrik-Überblick (Deutsch), z. B. für /stats."""
    uptime = time.monotonic() - _BOOT_MONO
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    metrics_block = await BotMetrics().format_stats_text()
    return (
        f"⚙️ Status\n"
        f"Laufzeit: {_format_uptime(uptime)}\n"
        f"Python: {py}\n\n"
        f"{metrics_block}"
    )
