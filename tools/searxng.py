"""Async client for SearXNG meta-search (JSON API)."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import aiohttp
from aiohttp import ClientTimeout

from shared.exceptions import ResearchError
from shared.types import (
    CitedStatement,
    ConfidenceLevel,
    ResearchResult,
    Source,
    StatementType,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = ClientTimeout(total=60)


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _pick_engine(hit: dict[str, Any]) -> str:
    eng = hit.get("engine")
    if isinstance(eng, str) and eng:
        return eng
    engines = hit.get("engines")
    if isinstance(engines, list) and engines:
        first = engines[0]
        if isinstance(first, str):
            return first
    return "searxng"


def _pick_content(hit: dict[str, Any]) -> str:
    for key in ("content", "snippet", "abstract"):
        val = hit.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


class SearXNGClient:
    """Query a self-hosted SearXNG instance and map hits to `ResearchResult`."""

    tool_name = "searxng"

    def __init__(self, base_url: str = "http://searxng:8080") -> None:
        self._base = _normalize_base_url(base_url)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def _failure(
        self,
        message: str,
        started: float,
        raw: dict[str, Any] | None = None,
    ) -> ResearchResult:
        err = ResearchError(self.tool_name, message)
        logger.warning("%s", err)
        return ResearchResult(
            tool_name=self.tool_name,
            statements=[],
            raw_data=raw or {},
            success=False,
            error_message=str(err),
            duration_seconds=time.monotonic() - started,
        )

    async def search(
        self,
        query: str,
        categories: str = "general",
        language: str = "de",
        time_range: str | None = None,
        max_results: int = 10,
    ) -> ResearchResult:
        started = time.monotonic()
        params: dict[str, str] = {
            "q": query,
            "format": "json",
            "categories": categories,
            "language": language,
        }
        if time_range is not None:
            params["time_range"] = time_range

        url = f"{self._base}/search?{urlencode(params)}"

        try:
            session = await self._get_session()
            async with session.get(url) as resp:
                if resp.status != 200:
                    body = await resp.read()
                    text = body.decode("utf-8", errors="replace")[:2000]
                    return self._failure(
                        f"HTTP {resp.status}: {text}",
                        started,
                        raw={"url": url, "status": resp.status, "body_preview": text},
                    )
                try:
                    data: Any = await resp.json()
                except Exception as exc:
                    logger.warning("SearXNG response was not valid JSON", exc_info=True)
                    return self._failure(
                        f"Invalid JSON: {exc}",
                        started,
                        raw={"url": url},
                    )
        except asyncio.TimeoutError as exc:
            logger.warning("SearXNG request timed out", exc_info=True)
            return self._failure(f"Request timeout: {exc}", started)
        except aiohttp.ClientError as exc:
            logger.warning("SearXNG HTTP client error", exc_info=True)
            return self._failure(f"HTTP client error: {exc}", started)
        except OSError as exc:
            logger.warning("SearXNG network error", exc_info=True)
            return self._failure(f"Network error: {exc}", started)

        if not isinstance(data, dict):
            return self._failure("Response JSON was not an object", started, raw={"data": data})

        results_raw = data.get("results")
        if not isinstance(results_raw, list):
            results_raw = []

        statements: list[CitedStatement] = []
        retrieved_at = datetime.utcnow()

        for hit in results_raw[:max_results]:
            if not isinstance(hit, dict):
                continue
            title = hit.get("title")
            link = hit.get("url") or hit.get("link") or ""
            if not isinstance(title, str):
                title = str(title) if title is not None else ""
            if not isinstance(link, str):
                link = str(link) if link is not None else ""

            snippet = _pick_content(hit)
            engine = _pick_engine(hit)

            source = Source(
                url=link or self._base,
                name=title or link or "result",
                snippet=snippet,
                retrieved_at=retrieved_at,
                source_type="searxng",
                extra={"engine": engine},
            )
            stmt = CitedStatement(
                text=title or snippet or link,
                statement_type=StatementType.FACT,
                confidence=ConfidenceLevel.MEDIUM,
                sources=[source],
                category=categories,
            )
            statements.append(stmt)

        duration = time.monotonic() - started
        return ResearchResult(
            tool_name=self.tool_name,
            statements=statements,
            raw_data=data if isinstance(data, dict) else {"response": data},
            success=True,
            error_message="",
            duration_seconds=duration,
        )

    async def search_news(self, query: str, max_results: int = 10) -> ResearchResult:
        return await self.search(
            query,
            categories="news",
            max_results=max_results,
        )

    async def search_academic(self, query: str, max_results: int = 10) -> ResearchResult:
        return await self.search(
            query,
            categories="science",
            max_results=max_results,
        )
