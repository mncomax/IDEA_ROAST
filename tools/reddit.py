"""Async Reddit API client (OAuth2 app-only) for research."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import aiohttp
from aiohttp import BasicAuth, ClientTimeout

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
TOOL_NAME = "reddit"

DEFAULT_BUSINESS_SUBREDDITS: list[str] = [
    "startups",
    "Entrepreneur",
    "SaaS",
    "smallbusiness",
]

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_OAUTH_BASE = "https://oauth.reddit.com"


def _permalink_url(permalink: str) -> str:
    if not permalink:
        return ""
    if permalink.startswith("http"):
        return permalink
    return f"https://www.reddit.com{permalink}"


def _confidence_from_score(score: int) -> ConfidenceLevel:
    if score > 100:
        return ConfidenceLevel.HIGH
    if score > 10:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


class RedditClient:
    """Reddit search with OAuth2 client-credentials and lazy `aiohttp` session."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        user_agent: str = "IdeaRoast/1.0",
    ) -> None:
        self._client_id = (client_id or "").strip()
        self._client_secret = (client_secret or "").strip()
        self._user_agent = user_agent
        self._session: aiohttp.ClientSession | None = None
        self._access_token: str | None = None
        self._token_expires_at_monotonic: float = 0.0

    def _credentials_ok(self) -> bool:
        return bool(self._client_id and self._client_secret)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"User-Agent": self._user_agent}
            self._session = aiohttp.ClientSession(
                timeout=DEFAULT_TIMEOUT,
                headers=headers,
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._access_token = None
        self._token_expires_at_monotonic = 0.0

    async def _ensure_token(self) -> str | None:
        if not self._credentials_ok():
            return None
        now = time.monotonic()
        if self._access_token and now < self._token_expires_at_monotonic - 30:
            return self._access_token

        session = await self._get_session()
        try:
            async with session.post(
                _TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=BasicAuth(self._client_id, self._client_secret),
                headers={"User-Agent": self._user_agent},
            ) as resp:
                if resp.status != 200:
                    text = (await resp.text())[:2000]
                    logger.warning("Reddit token HTTP %s: %s", resp.status, text)
                    return None
                try:
                    data: Any = await resp.json(content_type=None)
                except Exception as exc:
                    logger.warning("Reddit token JSON error: %s", exc)
                    return None
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as exc:
            logger.warning("Reddit token request failed: %s", exc)
            return None

        if not isinstance(data, dict):
            return None
        token = data.get("access_token")
        expires_in = data.get("expires_in")
        if not isinstance(token, str) or not token:
            return None
        self._access_token = token
        try:
            sec = float(expires_in) if expires_in is not None else 3600.0
        except (TypeError, ValueError):
            sec = 3600.0
        self._token_expires_at_monotonic = time.monotonic() + max(60.0, sec)
        return self._access_token

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

    def _post_to_statement(self, p: dict[str, Any], retrieved_at: datetime) -> CitedStatement | None:
        title = p.get("title")
        if not isinstance(title, str):
            title = str(title) if title is not None else ""
        selftext = p.get("selftext")
        if not isinstance(selftext, str):
            selftext = str(selftext) if selftext is not None else ""
        snippet_200 = selftext[:200] if selftext else ""
        text = (title + (" " + snippet_200 if snippet_200 else "")).strip()
        if not text:
            return None

        permalink = p.get("permalink")
        permalink_s = permalink if isinstance(permalink, str) else ""
        url = _permalink_url(permalink_s) or (p.get("url") if isinstance(p.get("url"), str) else "")

        score_raw = p.get("score", 0)
        try:
            score = int(score_raw) if score_raw is not None else 0
        except (TypeError, ValueError):
            score = 0

        num_comments_raw = p.get("num_comments", 0)
        try:
            num_comments = int(num_comments_raw) if num_comments_raw is not None else 0
        except (TypeError, ValueError):
            num_comments = 0

        sub = p.get("subreddit")
        sub_s = sub if isinstance(sub, str) else str(sub) if sub is not None else ""

        source = Source(
            url=url or "https://www.reddit.com",
            name=title or "reddit post",
            snippet=snippet_200,
            retrieved_at=retrieved_at,
            source_type="reddit",
            extra={
                "score": score,
                "num_comments": num_comments,
                "subreddit": sub_s,
            },
        )
        return CitedStatement(
            text=text,
            statement_type=StatementType.FACT,
            confidence=_confidence_from_score(score),
            sources=[source],
            category="sentiment",
        )

    def _parse_listing(self, payload: Any, retrieved_at: datetime) -> list[CitedStatement]:
        out: list[CitedStatement] = []
        if not isinstance(payload, dict):
            return out
        data = payload.get("data")
        if not isinstance(data, dict):
            return out
        children = data.get("children")
        if not isinstance(children, list):
            return out
        for ch in children:
            if not isinstance(ch, dict) or ch.get("kind") != "t3":
                continue
            inner = ch.get("data")
            if not isinstance(inner, dict):
                continue
            stmt = self._post_to_statement(inner, retrieved_at)
            if stmt is not None:
                out.append(stmt)
        return out

    async def _search_one_url(self, session: aiohttp.ClientSession, url: str, headers: dict[str, str]) -> Any:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                text = (await resp.text())[:2000]
                raise ResearchError(TOOL_NAME, f"HTTP {resp.status}: {text}")
            try:
                return await resp.json(content_type=None)
            except Exception as exc:
                raise ResearchError(TOOL_NAME, f"Invalid JSON: {exc}") from exc

    async def search(
        self,
        query: str,
        subreddits: list[str] | None = None,
        sort: str = "relevance",
        time_filter: str = "year",
        limit: int = 25,
    ) -> ResearchResult:
        started = time.monotonic()
        retrieved_at = datetime.utcnow()

        if not self._credentials_ok():
            return self._failure(
                "Missing reddit_client_id or reddit_client_secret",
                started,
                raw={"hint": "set env reddit_client_id, reddit_client_secret, reddit_user_agent"},
            )

        token = await self._ensure_token()
        if not token:
            return self._failure("Could not obtain Reddit OAuth token", started)

        # None → global /search; a provided list → /r/{sub}/search per sub (see DEFAULT_BUSINESS_SUBREDDITS).
        use_global = subreddits is None or len(subreddits) == 0
        subs_to_search = [s.strip().strip("/") for s in (subreddits or []) if s and str(s).strip()]

        params_base: dict[str, Any] = {
            "q": query,
            "sort": sort,
            "t": time_filter,
            "limit": min(max(limit, 1), 100),
        }

        headers = {
            "User-Agent": self._user_agent,
            "Authorization": f"bearer {token}",
        }

        raw_batches: list[Any] = []
        statements: list[CitedStatement] = []

        try:
            session = await self._get_session()

            if use_global:
                params = dict(params_base)
                url = f"{_OAUTH_BASE}/search?{urlencode(params)}"
                data = await self._search_one_url(session, url, headers)
                raw_batches.append(data)
                statements.extend(self._parse_listing(data, retrieved_at))
            else:
                for sub_clean in subs_to_search:
                    params = dict(params_base)
                    params["restrict_sr"] = "1"
                    url = f"{_OAUTH_BASE}/r/{sub_clean}/search?{urlencode(params)}"
                    data = await self._search_one_url(session, url, headers)
                    raw_batches.append({"subreddit": sub_clean, "listing": data})
                    statements.extend(self._parse_listing(data, retrieved_at))
        except ResearchError as err:
            msg = str(err)
            prefix = f"[{TOOL_NAME}] "
            if msg.startswith(prefix):
                msg = msg[len(prefix) :]
            return self._failure(msg, started, raw={"batches": raw_batches})
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as exc:
            return self._failure(str(exc), started, raw={"batches": raw_batches})

        duration = time.monotonic() - started
        return ResearchResult(
            tool_name=TOOL_NAME,
            statements=statements,
            raw_data={
                "responses": raw_batches,
                "query": query,
                "subreddits": None if use_global else subs_to_search,
            },
            success=True,
            error_message="",
            duration_seconds=duration,
        )

    async def get_subreddit_about(self, subreddit: str) -> dict[str, Any]:
        if not self._credentials_ok():
            raise ResearchError(TOOL_NAME, "Missing reddit_client_id or reddit_client_secret")

        token = await self._ensure_token()
        if not token:
            raise ResearchError(TOOL_NAME, "Could not obtain Reddit OAuth token")

        sub = subreddit.strip().strip("/")
        if not sub:
            raise ResearchError(TOOL_NAME, "Empty subreddit name")

        url = f"{_OAUTH_BASE}/r/{sub}/about"
        headers = {
            "User-Agent": self._user_agent,
            "Authorization": f"bearer {token}",
        }
        session = await self._get_session()
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    text = (await resp.text())[:2000]
                    raise ResearchError(TOOL_NAME, f"HTTP {resp.status}: {text}")
                try:
                    data: Any = await resp.json(content_type=None)
                except Exception as exc:
                    raise ResearchError(TOOL_NAME, f"Invalid JSON: {exc}") from exc
        except ResearchError:
            raise
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as exc:
            raise ResearchError(TOOL_NAME, str(exc)) from exc

        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            return data["data"]
        if isinstance(data, dict):
            return data
        return {"raw": data}
