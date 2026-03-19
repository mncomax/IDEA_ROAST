"""Async Product Hunt research via GraphQL or SearXNG fallback."""

from __future__ import annotations

import asyncio
import logging
import re
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
TOOL_NAME = "producthunt"
GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"

_PH_GRAPHQL_QUERY = """
query Posts($topic: String!, $first: Int!) {
  posts(topic: $topic, order: VOTES, first: $first) {
    edges {
      node {
        name
        tagline
        url
        votesCount
        commentsCount
        createdAt
        topics {
          edges {
            node {
              name
            }
          }
        }
      }
    }
  }
}
"""


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _topic_slug(query: str) -> str:
    q = query.strip().lower()
    q = re.sub(r"[^a-z0-9]+", "-", q).strip("-")
    return q or "tech"


def _pick_content(hit: dict[str, Any]) -> str:
    for key in ("content", "snippet", "abstract"):
        val = hit.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


class ProductHuntClient:
    """Product Hunt GraphQL with optional SearXNG fallback (no token)."""

    def __init__(
        self,
        developer_token: str | None = None,
        searxng_base_url: str | None = None,
    ) -> None:
        self._token = (developer_token or "").strip() or None
        self._searxng_base = (
            _normalize_base_url(searxng_base_url.strip())
            if searxng_base_url and searxng_base_url.strip()
            else None
        )
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

    def _posts_to_statements(
        self,
        nodes: list[dict[str, Any]],
        retrieved_at: datetime,
    ) -> list[CitedStatement]:
        statements: list[CitedStatement] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            name = node.get("name")
            tag = node.get("tagline")
            name_s = name if isinstance(name, str) else str(name or "")
            tag_s = tag if isinstance(tag, str) else str(tag or "")
            text = f"{name_s}: {tag_s}".strip() if tag_s else name_s
            if not text:
                continue

            url = node.get("url")
            link = url if isinstance(url, str) and url.startswith("http") else f"https://www.producthunt.com{url or ''}"

            votes_raw = node.get("votesCount", 0)
            comments_raw = node.get("commentsCount", 0)
            try:
                votes = int(votes_raw) if votes_raw is not None else 0
            except (TypeError, ValueError):
                votes = 0
            try:
                comments = int(comments_raw) if comments_raw is not None else 0
            except (TypeError, ValueError):
                comments = 0

            topic_names: list[str] = []
            topics = node.get("topics")
            if isinstance(topics, dict):
                edges = topics.get("edges")
                if isinstance(edges, list):
                    for edge in edges:
                        if not isinstance(edge, dict):
                            continue
                        n = edge.get("node")
                        if isinstance(n, dict) and isinstance(n.get("name"), str):
                            topic_names.append(n["name"])

            source = Source(
                url=link,
                name=name_s or "Product Hunt",
                snippet=tag_s[:500],
                retrieved_at=retrieved_at,
                source_type="producthunt",
                extra={
                    "votes": votes,
                    "comments": comments,
                    "topics": topic_names,
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
        return statements

    async def _search_graphql(
        self,
        query: str,
        max_results: int,
        started: float,
        retrieved_at: datetime,
    ) -> ResearchResult:
        assert self._token is not None
        session = await self._get_session()
        topic = _topic_slug(query)
        first = min(max(max_results, 1), 50)
        payload = {
            "query": _PH_GRAPHQL_QUERY,
            "variables": {"topic": topic, "first": first},
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        try:
            async with session.post(GRAPHQL_URL, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    text = (await resp.text())[:2000]
                    return self._failure(
                        f"GraphQL HTTP {resp.status}: {text}",
                        started,
                        raw={"topic": topic},
                    )
                try:
                    body: Any = await resp.json(content_type=None)
                except Exception as exc:
                    return self._failure(f"Invalid JSON: {exc}", started)
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as exc:
            return self._failure(str(exc), started)

        if not isinstance(body, dict):
            return self._failure("GraphQL response was not an object", started, raw={"body": body})

        errs = body.get("errors")
        if isinstance(errs, list) and errs:
            msg = str(errs[0]) if errs else "GraphQL errors"
            return self._failure(msg, started, raw={"errors": errs})

        data = body.get("data")
        nodes: list[dict[str, Any]] = []
        if isinstance(data, dict):
            posts = data.get("posts")
            if isinstance(posts, dict):
                edges = posts.get("edges")
                if isinstance(edges, list):
                    for edge in edges:
                        if isinstance(edge, dict):
                            n = edge.get("node")
                            if isinstance(n, dict):
                                nodes.append(n)

        statements = self._posts_to_statements(nodes, retrieved_at)
        duration = time.monotonic() - started
        return ResearchResult(
            tool_name=TOOL_NAME,
            statements=statements,
            raw_data={"graphql": body, "topic_slug": topic},
            success=True,
            error_message="",
            duration_seconds=duration,
        )

    async def _search_searxng(
        self,
        query: str,
        max_results: int,
        started: float,
        retrieved_at: datetime,
    ) -> ResearchResult:
        assert self._searxng_base is not None
        q = f"site:producthunt.com {query}"
        params = {"q": q, "format": "json", "categories": "general", "language": "en"}
        url = f"{self._searxng_base}/search?{urlencode(params)}"
        try:
            session = await self._get_session()
            async with session.get(url) as resp:
                if resp.status != 200:
                    text = (await resp.text())[:2000]
                    return self._failure(
                        f"SearXNG HTTP {resp.status}: {text}",
                        started,
                        raw={"url": url},
                    )
                try:
                    data: Any = await resp.json(content_type=None)
                except Exception as exc:
                    return self._failure(f"Invalid JSON: {exc}", started, raw={"url": url})
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as exc:
            return self._failure(str(exc), started)

        if not isinstance(data, dict):
            return self._failure("SearXNG JSON was not an object", started)

        results_raw = data.get("results")
        if not isinstance(results_raw, list):
            results_raw = []

        statements: list[CitedStatement] = []
        for hit in results_raw[:max_results]:
            if not isinstance(hit, dict):
                continue
            link = hit.get("url") or hit.get("link") or ""
            if not isinstance(link, str) or "producthunt.com" not in link.lower():
                continue
            title = hit.get("title")
            title_s = title if isinstance(title, str) else str(title or "")
            snippet = _pick_content(hit)
            text = (title_s + (" — " + snippet if snippet else "")).strip()
            if not text:
                continue

            source = Source(
                url=link,
                name=title_s or "Product Hunt",
                snippet=snippet[:500],
                retrieved_at=retrieved_at,
                source_type="producthunt",
                extra={"votes": 0, "comments": 0, "topics": [], "via": "searxng"},
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
            raw_data={"searxng": data, "query": query},
            success=True,
            error_message="",
            duration_seconds=duration,
        )

    async def search(self, query: str, max_results: int = 10) -> ResearchResult:
        started = time.monotonic()
        retrieved_at = datetime.utcnow()

        if self._token:
            return await self._search_graphql(query, max_results, started, retrieved_at)

        if self._searxng_base:
            return await self._search_searxng(query, max_results, started, retrieved_at)

        return self._failure(
            "No Product Hunt token and no SearXNG base URL configured",
            started,
            raw={"hint": "set developer_token or searxng_base_url"},
        )
