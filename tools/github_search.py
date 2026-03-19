"""Async GitHub Search API client for repository and topic research."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import quote_plus

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
TOOL_NAME = "github"
API_BASE = "https://api.github.com"


def _stars_confidence(stars: int) -> ConfidenceLevel:
    if stars > 1000:
        return ConfidenceLevel.HIGH
    if stars > 100:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def _rate_limit_remaining(headers: Mapping[str, Any]) -> int | None:
    raw = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
    if raw is None:
        return None
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


class GitHubSearchClient:
    """GitHub Search API with optional token and lazy `aiohttp` session."""

    def __init__(self, token: str | None = None) -> None:
        self._token = (token or "").strip() or None
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _failure(
        self,
        message: str,
        started: float,
        raw: dict[str, Any] | None = None,
    ) -> ResearchResult:
        err = ResearchError(TOOL_NAME, message)
        logger.warning("%s", err)
        return ResearchResult(
            tool_name=TOOL_NAME,
            statements=[],
            raw_data=raw or {},
            success=False,
            error_message=str(err),
            duration_seconds=time.monotonic() - started,
        )

    async def search_repos(
        self,
        query: str,
        sort: str = "stars",
        max_results: int = 15,
    ) -> ResearchResult:
        started = time.monotonic()
        retrieved_at = datetime.utcnow()
        per_page = min(max(max_results, 1), 100)
        url = (
            f"{API_BASE}/search/repositories?"
            f"q={quote_plus(query)}&sort={quote_plus(sort)}&per_page={per_page}"
        )

        try:
            session = await self._get_session()
            async with session.get(url, headers=self._headers()) as resp:
                rem = _rate_limit_remaining(resp.headers)
                if resp.status != 200:
                    text = (await resp.text())[:2000]
                    if rem is not None and rem == 0:
                        raise ResearchError(
                            TOOL_NAME,
                            "GitHub API rate limit exhausted (X-RateLimit-Remaining: 0)",
                        )
                    if resp.status in (403, 429):
                        raise ResearchError(
                            TOOL_NAME,
                            f"GitHub API rate limit or forbidden: HTTP {resp.status}: {text}",
                        )
                    return self._failure(
                        f"HTTP {resp.status}: {text}",
                        started,
                        raw={"url": url, "status": resp.status},
                    )
                try:
                    data: Any = await resp.json(content_type=None)
                except Exception as exc:
                    return self._failure(f"Invalid JSON: {exc}", started, raw={"url": url})
        except ResearchError as err:
            msg = str(err)
            prefix = f"[{TOOL_NAME}] "
            if msg.startswith(prefix):
                msg = msg[len(prefix) :]
            return self._failure(msg, started)
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as exc:
            return self._failure(str(exc), started)

        if not isinstance(data, dict):
            return self._failure("Response JSON was not an object", started, raw={"data": data})

        items = data.get("items")
        if not isinstance(items, list):
            items = []

        statements: list[CitedStatement] = []
        for it in items[:per_page]:
            if not isinstance(it, dict):
                continue
            full_name = it.get("full_name")
            fn = full_name if isinstance(full_name, str) else str(full_name or "")
            desc = it.get("description")
            desc_s = desc if isinstance(desc, str) else (str(desc) if desc is not None else "")
            text = f"{fn}: {desc_s}" if desc_s else fn
            if not text.strip():
                continue

            html_url = it.get("html_url")
            link = html_url if isinstance(html_url, str) else "https://github.com"

            stars_raw = it.get("stargazers_count", 0)
            try:
                stars = int(stars_raw) if stars_raw is not None else 0
            except (TypeError, ValueError):
                stars = 0

            forks_raw = it.get("forks_count", 0)
            try:
                forks = int(forks_raw) if forks_raw is not None else 0
            except (TypeError, ValueError):
                forks = 0

            lang = it.get("language")
            lang_s = lang if isinstance(lang, str) else None

            topics = it.get("topics")
            topic_list: list[str] = []
            if isinstance(topics, list):
                topic_list = [str(t) for t in topics if isinstance(t, str)]

            source = Source(
                url=link,
                name=fn or "repository",
                snippet=desc_s[:500],
                retrieved_at=retrieved_at,
                source_type="github",
                extra={
                    "stars": stars,
                    "forks": forks,
                    "language": lang_s,
                    "topics": topic_list,
                    "created_at": it.get("created_at"),
                    "updated_at": it.get("updated_at"),
                },
            )
            statements.append(
                CitedStatement(
                    text=text.strip(),
                    statement_type=StatementType.FACT,
                    confidence=_stars_confidence(stars),
                    sources=[source],
                    category="competition",
                )
            )

        duration = time.monotonic() - started
        return ResearchResult(
            tool_name=TOOL_NAME,
            statements=statements,
            raw_data=data if isinstance(data, dict) else {"response": data},
            success=True,
            error_message="",
            duration_seconds=duration,
        )

    async def search_topics(self, query: str) -> ResearchResult:
        started = time.monotonic()
        retrieved_at = datetime.utcnow()
        url = f"{API_BASE}/search/topics?q={quote_plus(query)}"
        headers = self._headers()
        headers["Accept"] = "application/vnd.github.mercy-preview+json"

        try:
            session = await self._get_session()
            async with session.get(url, headers=headers) as resp:
                rem = _rate_limit_remaining(resp.headers)
                if resp.status != 200:
                    text = (await resp.text())[:2000]
                    if rem is not None and rem == 0:
                        raise ResearchError(
                            TOOL_NAME,
                            "GitHub API rate limit exhausted (X-RateLimit-Remaining: 0)",
                        )
                    if resp.status in (403, 429):
                        raise ResearchError(
                            TOOL_NAME,
                            f"GitHub API rate limit or forbidden: HTTP {resp.status}: {text}",
                        )
                    return self._failure(
                        f"HTTP {resp.status}: {text}",
                        started,
                        raw={"url": url, "status": resp.status},
                    )
                try:
                    data: Any = await resp.json(content_type=None)
                except Exception as exc:
                    return self._failure(f"Invalid JSON: {exc}", started, raw={"url": url})
        except ResearchError as err:
            msg = str(err)
            prefix = f"[{TOOL_NAME}] "
            if msg.startswith(prefix):
                msg = msg[len(prefix) :]
            return self._failure(msg, started)
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as exc:
            return self._failure(str(exc), started)

        if not isinstance(data, dict):
            return self._failure("Response JSON was not an object", started, raw={"data": data})

        items = data.get("items")
        if not isinstance(items, list):
            items = []

        statements: list[CitedStatement] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = it.get("name", "")
            display = it.get("display_name", name)
            disp_s = display if isinstance(display, str) else str(display)
            desc = it.get("description") or it.get("short_description") or ""
            desc_s = desc if isinstance(desc, str) else str(desc)
            text = f"{disp_s}: {desc_s}".strip() if desc_s else disp_s
            if not text:
                continue

            gh_url = f"https://github.com/topics/{name}" if isinstance(name, str) and name else "https://github.com/topics"

            curated = it.get("curated")
            featured = it.get("featured")
            source = Source(
                url=gh_url,
                name=disp_s,
                snippet=desc_s[:500],
                retrieved_at=retrieved_at,
                source_type="github",
                extra={
                    "curated": bool(curated) if curated is not None else False,
                    "featured": bool(featured) if featured is not None else False,
                    "display_name": disp_s,
                    "description": desc_s,
                },
            )
            statements.append(
                CitedStatement(
                    text=text,
                    statement_type=StatementType.FACT,
                    confidence=ConfidenceLevel.MEDIUM,
                    sources=[source],
                    category="competition",
                )
            )

        duration = time.monotonic() - started
        return ResearchResult(
            tool_name=TOOL_NAME,
            statements=statements,
            raw_data=data if isinstance(data, dict) else {"response": data},
            success=True,
            error_message="",
            duration_seconds=duration,
        )
