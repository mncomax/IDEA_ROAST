"""Async client for the Hacker News Algolia search API."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

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

HN_ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"
DEFAULT_TIMEOUT = ClientTimeout(total=60)


def _story_url(hit: dict[str, Any]) -> str:
    url = hit.get("url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    oid = hit.get("objectID") or hit.get("story_id")
    if oid is not None:
        return f"https://news.ycombinator.com/item?id={oid}"
    return "https://news.ycombinator.com/"


def _comment_url(hit: dict[str, Any]) -> str:
    oid = hit.get("objectID")
    if oid is not None:
        return f"https://news.ycombinator.com/item?id={oid}"
    return "https://news.ycombinator.com/"


def _points(hit: dict[str, Any]) -> int:
    p = hit.get("points")
    if isinstance(p, int):
        return p
    if isinstance(p, float):
        return int(p)
    try:
        return int(p) if p is not None else 0
    except (TypeError, ValueError):
        return 0


def _confidence_from_points(points: int) -> ConfidenceLevel:
    return ConfidenceLevel.MEDIUM if points > 10 else ConfidenceLevel.LOW


class HackerNewsClient:
    """Search HN stories and comments via the public Algolia API."""

    tool_name = "hackernews"

    def __init__(self) -> None:
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
        time_range_months: int = 24,
        max_results: int = 20,
    ) -> ResearchResult:
        started = time.monotonic()
        params: dict[str, str | int] = {
            "query": query,
            "tags": "story",
            "hitsPerPage": max_results,
        }
        if time_range_months > 0:
            cutoff_dt = datetime.now(timezone.utc) - timedelta(
                days=time_range_months * 30
            )
            cutoff_ts = int(cutoff_dt.timestamp())
            params["numericFilters"] = f"created_at_i>{cutoff_ts}"

        try:
            session = await self._get_session()
            async with session.get(HN_ALGOLIA_SEARCH, params=params) as resp:
                if resp.status != 200:
                    body = await resp.read()
                    text = body.decode("utf-8", errors="replace")[:2000]
                    return self._failure(
                        f"HTTP {resp.status}: {text}",
                        started,
                        raw={"status": resp.status, "body_preview": text},
                    )
                try:
                    payload: Any = await resp.json()
                except Exception as exc:
                    logger.exception("HackerNews JSON decode failed")
                    return self._failure(
                        f"Invalid JSON: {exc}",
                        started,
                    )
        except asyncio.TimeoutError as exc:
            logger.exception("HackerNews search timeout")
            return self._failure(f"Request timeout: {exc}", started)
        except aiohttp.ClientError as exc:
            logger.exception("HackerNews search client error")
            return self._failure(f"HTTP client error: {exc}", started)
        except OSError as exc:
            logger.exception("HackerNews search OS error")
            return self._failure(f"Network error: {exc}", started)

        if not isinstance(payload, dict):
            return self._failure("Response JSON was not an object", started)

        hits_raw = payload.get("hits")
        if not isinstance(hits_raw, list):
            hits_raw = []

        hits = [h for h in hits_raw if isinstance(h, dict)]
        hits.sort(key=_points, reverse=True)
        hits = hits[:max_results]

        statements: list[CitedStatement] = []
        retrieved_at = datetime.utcnow()

        for hit in hits:
            title = hit.get("title")
            if not isinstance(title, str):
                title = str(title) if title is not None else ""
            url = _story_url(hit)
            pts = _points(hit)
            num_comments = hit.get("num_comments")
            if not isinstance(num_comments, int):
                try:
                    num_comments = int(num_comments) if num_comments is not None else 0
                except (TypeError, ValueError):
                    num_comments = 0

            created = hit.get("created_at_i")
            if created is None:
                created = hit.get("created_at")

            source = Source(
                url=url,
                name=title or url,
                snippet=title,
                retrieved_at=retrieved_at,
                source_type="hackernews",
                extra={
                    "points": pts,
                    "num_comments": num_comments,
                    "objectID": hit.get("objectID"),
                    "created_at": created,
                },
            )
            statements.append(
                CitedStatement(
                    text=title or url,
                    statement_type=StatementType.FACT,
                    confidence=_confidence_from_points(pts),
                    sources=[source],
                    category="story",
                )
            )

        duration = time.monotonic() - started
        return ResearchResult(
            tool_name=self.tool_name,
            statements=statements,
            raw_data=payload,
            success=True,
            error_message="",
            duration_seconds=duration,
        )

    async def search_comments(
        self,
        query: str,
        max_results: int = 20,
    ) -> ResearchResult:
        started = time.monotonic()
        params: dict[str, str | int] = {
            "query": query,
            "tags": "comment",
            "hitsPerPage": max_results,
        }

        try:
            session = await self._get_session()
            async with session.get(HN_ALGOLIA_SEARCH, params=params) as resp:
                if resp.status != 200:
                    body = await resp.read()
                    text = body.decode("utf-8", errors="replace")[:2000]
                    return self._failure(
                        f"HTTP {resp.status}: {text}",
                        started,
                        raw={"status": resp.status, "body_preview": text},
                    )
                try:
                    payload: Any = await resp.json()
                except Exception as exc:
                    logger.exception("HackerNews comments JSON decode failed")
                    return self._failure(
                        f"Invalid JSON: {exc}",
                        started,
                    )
        except asyncio.TimeoutError as exc:
            logger.exception("HackerNews search_comments timeout")
            return self._failure(f"Request timeout: {exc}", started)
        except aiohttp.ClientError as exc:
            logger.exception("HackerNews search_comments client error")
            return self._failure(f"HTTP client error: {exc}", started)
        except OSError as exc:
            logger.exception("HackerNews search_comments OS error")
            return self._failure(f"Network error: {exc}", started)

        if not isinstance(payload, dict):
            return self._failure("Response JSON was not an object", started)

        hits_raw = payload.get("hits")
        if not isinstance(hits_raw, list):
            hits_raw = []

        hits = [h for h in hits_raw if isinstance(h, dict)]
        hits.sort(key=_points, reverse=True)
        hits = hits[:max_results]

        statements: list[CitedStatement] = []
        retrieved_at = datetime.utcnow()

        for hit in hits:
            text_body = hit.get("comment_text")
            if not isinstance(text_body, str):
                text_body = str(text_body) if text_body is not None else ""
            # Strip simple HTML tags often present in Algolia hits
            text_clean = text_body.replace("<p>", "").replace("</p>", "\n").strip()
            url = _comment_url(hit)
            pts = _points(hit)
            num_comments = hit.get("num_comments")
            if not isinstance(num_comments, int):
                try:
                    num_comments = int(num_comments) if num_comments is not None else 0
                except (TypeError, ValueError):
                    num_comments = 0

            title = hit.get("story_title")
            name = title if isinstance(title, str) else "HN comment"

            source = Source(
                url=url,
                name=name,
                snippet=text_clean[:500] + ("…" if len(text_clean) > 500 else ""),
                retrieved_at=retrieved_at,
                source_type="hackernews",
                extra={
                    "points": pts,
                    "num_comments": num_comments,
                    "objectID": hit.get("objectID"),
                    "story_id": hit.get("story_id"),
                    "author": hit.get("author"),
                },
            )
            statements.append(
                CitedStatement(
                    text=text_clean or url,
                    statement_type=StatementType.FACT,
                    confidence=_confidence_from_points(pts),
                    sources=[source],
                    category="comment",
                )
            )

        duration = time.monotonic() - started
        return ResearchResult(
            tool_name=self.tool_name,
            statements=statements,
            raw_data=payload,
            success=True,
            error_message="",
            duration_seconds=duration,
        )
