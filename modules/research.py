"""
Research orchestration — parallel tool calls, 3-tier fallback, DB + cache, citations.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from typing import Any

from db.repository import Repository
from llm.client import LLMClient
from modules.cache_manager import CacheManager
from shared.constants import PROGRESS_MESSAGES, RESEARCH_CACHE_TTL, TREND_LOOKBACK_QUARTERS
from shared.exceptions import LLMError, LLMResponseParsingError
from shared.types import (
    CitedStatement,
    ConfidenceLevel,
    IdeaSummary,
    ProgressCallback,
    ResearchBundle,
    ResearchResult,
    Source,
    StatementType,
    TrendRadarResult,
    TrendVerdict,
)
from tools.github_search import GitHubSearchClient
from tools.hackernews import HackerNewsClient
from tools.producthunt import ProductHuntClient
from tools.reddit import RedditClient
from tools.searxng import SearXNGClient
from tools.trend_radar import TrendRadar

logger = logging.getLogger(__name__)


RESEARCH_QUERIES_SYSTEM_PROMPT = """
You turn a structured business idea summary into effective web-research inputs.
Return a single JSON object with exactly these keys:
- market_query: one concise English web search string about the problem, market size, and demand.
- competitor_query: one English search string about existing solutions, competitors, and alternatives.
- sentiment_query: one English search string about discussions, opinions, pain, and reviews around this problem.
- trend_keywords: an array of 2-3 short English keywords or short phrases for multi-quarter trend analysis.
- reddit_subreddits: an array of 6-10 subreddit names (WITHOUT "r/" prefix) that are most relevant for this specific idea. Pick communities where this idea's target audience hangs out, where the problem is discussed, and where competitors/alternatives are talked about. Mix large popular subs with smaller niche ones. Examples: for a gaming idea → ["gaming", "indiegaming", "Steam", "gamedev", "pcgaming", "Games"]. For a B2B SaaS → ["SaaS", "startups", "Entrepreneur", "smallbusiness", "marketing", "sales"].

Rules:
- Be specific to the idea; avoid generic fluff.
- Prefer English queries — retrieval quality is usually better than German for these APIs.
- trend_keywords must be distinct facets (e.g. product category + core problem term).
- reddit_subreddits MUST contain at least 6 entries, tailored to the idea's domain.
""".strip()


def _display_name_for_tool(tool_name: str) -> str:
    mapping: dict[str, str] = {
        "searxng": "SearXNG",
        "searxng_market_general": "SearXNG (Markt)",
        "searxng_market_news": "SearXNG (News)",
        "searxng_academic": "SearXNG (Wissenschaft)",
        "searxng_fallback": "SearXNG (Fallback)",
        "reddit": "Reddit (Subreddits)",
        "reddit_global": "Reddit (Global)",
        "reddit_searxng": "Reddit (via SearXNG)",
        "hackernews": "Hacker News",
        "hackernews_comments": "Hacker News (Kommentare)",
        "github": "GitHub",
        "producthunt": "Product Hunt",
        "trend_radar": "Trend-Radar",
    }
    return mapping.get(tool_name, tool_name.replace("_", " ").title())


def _sanitize_for_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(x) for x in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    return obj


def _research_result_to_cache_payload(result: ResearchResult) -> dict[str, Any]:
    return _sanitize_for_json(asdict(result))


FALLBACK_SUBREDDITS = [
    "startups", "Entrepreneur", "smallbusiness", "SaaS", "business", "marketing",
]


def _fallback_queries(summary: IdeaSummary) -> dict[str, str | list[str]]:
    base = (summary.problem_statement or summary.solution or "startup idea").strip()
    sol = (summary.solution or "product").strip()
    return {
        "market_query": f"{base} market demand TAM",
        "competitor_query": f"{sol} competitors alternatives",
        "sentiment_query": f"{base} discussion reviews frustration",
        "trend_keywords": [w for w in [sol.split()[0] if sol else "", "b2b", "saas"] if w][:3],
        "reddit_subreddits": list(FALLBACK_SUBREDDITS),
    }


class ResearchModule:
    """Orchestrates multi-source research with resilience and user-visible progress."""

    def __init__(
        self,
        searxng: SearXNGClient,
        reddit: RedditClient,
        hackernews: HackerNewsClient,
        github: GitHubSearchClient,
        producthunt: ProductHuntClient,
        trend_radar: TrendRadar,
        llm: LLMClient,
        repo: Repository,
    ) -> None:
        self._searxng = searxng
        self._reddit = reddit
        self._hackernews = hackernews
        self._github = github
        self._producthunt = producthunt
        self._trend_radar = trend_radar
        self._llm = llm
        self._repo = repo
        self._cache = CacheManager(repo)

    async def _cached_search(
        self,
        tool_name: str,
        query: str,
        search_awaitable: Any,
        idea_id: int,
    ) -> ResearchResult:
        async def fetch() -> ResearchResult:
            return await self._safe_search(search_awaitable, tool_name)

        return await self._cache.get_cached_or_fetch(
            tool_name, query, fetch, idea_id=idea_id
        )

    async def _notify(self, progress: ProgressCallback | None, message_key: str) -> None:
        if progress is None:
            return
        text = PROGRESS_MESSAGES.get(message_key, message_key)
        await progress(text)

    async def _notify_source_failed(
        self, progress: ProgressCallback | None, tool_name: str
    ) -> None:
        if progress is None:
            return
        template = PROGRESS_MESSAGES.get("source_failed", "{source} nicht erreichbar")
        label = _display_name_for_tool(tool_name)
        await progress(template.format(source=label))

    def _generate_queries_sync_fallback(self, summary: IdeaSummary) -> dict[str, str | list[str]]:
        logger.warning("Using heuristic fallback queries (LLM unavailable or failed)")
        return _fallback_queries(summary)

    async def _generate_queries(self, summary: IdeaSummary) -> dict[str, str | list[str]]:
        user_message = (
            f"problem_statement:\n{summary.problem_statement}\n\n"
            f"target_audience:\n{summary.target_audience}\n\n"
            f"solution:\n{summary.solution}\n\n"
            f"monetization:\n{summary.monetization}\n\n"
            f"distribution_channel:\n{summary.distribution_channel}\n\n"
            f"unfair_advantage:\n{summary.unfair_advantage}\n"
        )
        try:
            data = await self._llm.complete_structured(
                system_prompt=RESEARCH_QUERIES_SYSTEM_PROMPT,
                user_message=user_message,
                task="research_extract",
            )
        except (LLMError, LLMResponseParsingError, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.exception("LLM query extraction failed: %s", exc)
            return self._generate_queries_sync_fallback(summary)

        if not isinstance(data, dict):
            return self._generate_queries_sync_fallback(summary)

        market = data.get("market_query")
        competitor = data.get("competitor_query")
        sentiment = data.get("sentiment_query")
        trend_kw = data.get("trend_keywords")

        if not isinstance(market, str) or not market.strip():
            market = str(_fallback_queries(summary)["market_query"])
        if not isinstance(competitor, str) or not competitor.strip():
            competitor = str(_fallback_queries(summary)["competitor_query"])
        if not isinstance(sentiment, str) or not sentiment.strip():
            sentiment = str(_fallback_queries(summary)["sentiment_query"])

        keywords: list[str]
        if isinstance(trend_kw, list):
            keywords = [str(x).strip() for x in trend_kw if str(x).strip()][:5]
        elif isinstance(trend_kw, str) and trend_kw.strip():
            keywords = [trend_kw.strip()]
        else:
            keywords = list(_fallback_queries(summary)["trend_keywords"])  # type: ignore[assignment]

        if len(keywords) < 2:
            extra = _fallback_queries(summary)["trend_keywords"]
            if isinstance(extra, list):
                for k in extra:
                    if k not in keywords:
                        keywords.append(k)
                    if len(keywords) >= 3:
                        break

        raw_subs = data.get("reddit_subreddits")
        subreddits: list[str]
        if isinstance(raw_subs, list) and len(raw_subs) >= 3:
            subreddits = [str(s).strip().strip("/").replace("r/", "") for s in raw_subs if str(s).strip()][:10]
        else:
            subreddits = list(FALLBACK_SUBREDDITS)

        if len(subreddits) < 6:
            for fb in FALLBACK_SUBREDDITS:
                if fb not in subreddits:
                    subreddits.append(fb)
                if len(subreddits) >= 6:
                    break

        return {
            "market_query": market.strip(),
            "competitor_query": competitor.strip(),
            "sentiment_query": sentiment.strip(),
            "trend_keywords": keywords[:3],
            "reddit_subreddits": subreddits,
        }

    async def _safe_search(self, coro: Any, tool_name: str) -> ResearchResult:
        try:
            result = await coro
            if not isinstance(result, ResearchResult):
                logger.error("Tool %s returned non-ResearchResult: %s", tool_name, type(result))
                return ResearchResult(
                    tool_name=tool_name,
                    success=False,
                    error_message=f"Invalid return type: {type(result)}",
                    duration_seconds=0.0,
                )
            # Normalize tool label for downstream reporting
            result.tool_name = tool_name
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Tool search failed tool_name=%s", tool_name)
            return ResearchResult(
                tool_name=tool_name,
                success=False,
                error_message=str(exc),
                duration_seconds=0.0,
            )

    def _downgrade_confidence_tier2(self, results: list[ResearchResult]) -> None:
        for res in results:
            if not res.success:
                continue
            for stmt in res.statements:
                if stmt.confidence == ConfidenceLevel.HIGH:
                    stmt.confidence = ConfidenceLevel.MEDIUM

    def _append_tier_notes(
        self,
        results: list[ResearchResult],
        tier: int,
        gaps: bool,
    ) -> None:
        if tier == 1 and not gaps:
            return
        lines: list[str] = []
        if tier == 2 or gaps:
            lines.append(
                "Hinweis: Einige geplante Recherche-Quellen lieferten keine Daten — "
                "Aussagen stützen sich auf die verbleibenden Quellen; Konfidenz wurde angepasst."
            )
        if tier == 3:
            lines.append(
                "Warnung: Datenlage duenn — nur begrenzte oder Ersatz-Suchen lieferten Treffer; "
                "Ergebnisse sind mit Vorsicht zu interpretieren."
            )
        text = " ".join(lines)
        if not text:
            return
        meta = ResearchResult(
            tool_name="research_meta",
            success=True,
            statements=[
                CitedStatement(
                    text=text,
                    statement_type=StatementType.ESTIMATE,
                    confidence=ConfidenceLevel.LOW,
                    sources=[],
                    category="meta",
                )
            ],
            raw_data={"tier": tier, "gaps": gaps},
        )
        results.append(meta)

    async def _apply_fallback(
        self,
        results: list[ResearchResult],
        summary: IdeaSummary,
        progress: ProgressCallback | None,
    ) -> tuple[list[ResearchResult], int]:
        """Apply Tier 2/3 logic; may append extra SearXNG runs. Returns (results, tier)."""
        for res in results:
            if not res.success:
                await self._notify_source_failed(progress, res.tool_name)

        success_count = sum(1 for r in results if r.success)
        total = len(results)
        tier = 1
        gaps = success_count < total

        if success_count >= 2 and not gaps:
            return results, 1

        if success_count >= 2 and gaps:
            tier = 2
            self._downgrade_confidence_tier2(results)
            self._append_tier_notes(results, tier=2, gaps=True)
            return results, 2

        # Tier 3 — broad SearXNG rescue
        tier = 3
        self._downgrade_confidence_tier2(results)
        broad_queries = [
            f"{summary.problem_statement} {summary.target_audience} market".strip(),
            f"{summary.solution} competitors alternatives reviews".strip(),
            f"{summary.problem_statement} {summary.distribution_channel} landscape".strip(),
        ]
        fallback_tasks = [
            self._safe_search(
                self._searxng.search(q, categories="general", language="en", max_results=12),
                "searxng_fallback",
            )
            for q in broad_queries
            if q
        ]
        if fallback_tasks:
            extra = await asyncio.gather(*fallback_tasks)
            results.extend(extra)

        self._append_tier_notes(results, tier=3, gaps=True)
        return results, tier

    async def _save_sources_to_db(self, idea_id: int, results: list[ResearchResult]) -> int:
        saved = 0
        for res in results:
            if res.tool_name in ("research_meta", "research_orchestration"):
                continue
            for stmt in res.statements:
                for src in stmt.sources:
                    payload: dict[str, Any] = {
                        "url": src.url,
                        "name": src.name,
                        "snippet": src.snippet,
                        "source_type": src.source_type,
                        "confidence": stmt.confidence.value
                        if isinstance(stmt.confidence, ConfidenceLevel)
                        else str(stmt.confidence),
                        "category": stmt.category or res.tool_name,
                        "extra_json": src.extra,
                    }
                    await self._repo.save_source(idea_id, payload)
                    saved += 1
        return saved

    async def _save_trend_sources_to_db(self, idea_id: int, trend: TrendRadarResult) -> int:
        saved = 0
        for src in trend.sources:
            payload: dict[str, Any] = {
                "url": src.url,
                "name": src.name,
                "snippet": src.snippet,
                "source_type": src.source_type or "trend",
                "confidence": ConfidenceLevel.MEDIUM.value,
                "category": "trend",
                "extra_json": src.extra,
            }
            await self._repo.save_source(idea_id, payload)
            saved += 1
        return saved

    async def _cache_result(
        self,
        idea_id: int,
        tool_name: str,
        query_key: str,
        result: ResearchResult,
    ) -> None:
        if not result.success:
            return
        try:
            payload = _research_result_to_cache_payload(result)
            await self._repo.save_research_cache(
                idea_id,
                tool_name,
                query_key,
                payload,
                RESEARCH_CACHE_TTL,
            )
        except Exception:
            logger.exception("save_research_cache failed tool=%s", tool_name)

    async def run(
        self,
        idea_id: int,
        summary: IdeaSummary,
        progress: ProgressCallback | None = None,
    ) -> ResearchBundle:
        started_at = datetime.utcnow()
        await self._notify(progress, "research_start")

        try:
            queries = await self._generate_queries(summary)
        except (LLMError, LLMResponseParsingError):
            queries = self._generate_queries_sync_fallback(summary)
        except Exception:
            logger.exception("Unexpected error in _generate_queries")
            queries = self._generate_queries_sync_fallback(summary)

        market_q = str(queries["market_query"])
        competitor_q = str(queries["competitor_query"])
        sentiment_q = str(queries["sentiment_query"])
        trend_keywords = queries["trend_keywords"]
        if isinstance(trend_keywords, str):
            trend_kw_list = [trend_keywords]
        else:
            trend_kw_list = list(trend_keywords)

        raw_subs = queries.get("reddit_subreddits")
        reddit_subs: list[str] = list(raw_subs) if isinstance(raw_subs, list) else list(FALLBACK_SUBREDDITS)

        logger.info(
            "Research run idea_id=%s market_q=%r competitor_q=%r sentiment_q=%r keywords=%s subreddits=%s",
            idea_id,
            market_q,
            competitor_q,
            sentiment_q,
            trend_kw_list,
            reddit_subs,
        )

        # --- Phase 1a: market ---
        await self._notify(progress, "market_search")
        phase1_market = await asyncio.gather(
            self._cached_search(
                "searxng_market_general",
                market_q,
                self._searxng.search(market_q, categories="general", language="en"),
                idea_id,
            ),
            self._cached_search(
                "searxng_market_news",
                market_q,
                self._searxng.search(market_q, categories="news", language="en"),
                idea_id,
            ),
            self._cached_search(
                "searxng_academic",
                market_q,
                self._searxng.search(market_q, categories="science", language="en"),
                idea_id,
            ),
        )

        # --- Phase 1b: competitors ---
        await self._notify(progress, "competitor_search")
        phase1_comp = await asyncio.gather(
            self._cached_search(
                "github",
                competitor_q,
                self._github.search_repos(competitor_q),
                idea_id,
            ),
            self._cached_search(
                "producthunt",
                competitor_q,
                self._producthunt.search(competitor_q),
                idea_id,
            ),
        )

        phase1: list[ResearchResult] = list(phase1_market) + list(phase1_comp)

        # --- Phase 2: sentiment ---
        subs_display = ", ".join(f"r/{s}" for s in reddit_subs[:6])
        if progress:
            await progress(f"Durchsuche Reddit ({subs_display}), HN, Diskussionen...")

        reddit_has_credentials = self._reddit._credentials_ok() if hasattr(self._reddit, '_credentials_ok') else False

        reddit_tasks = []
        if reddit_has_credentials:
            reddit_tasks.append(self._cached_search(
                "reddit", sentiment_q,
                self._reddit.search(sentiment_q, subreddits=reddit_subs),
                idea_id,
            ))
            reddit_tasks.append(self._cached_search(
                "reddit_global", sentiment_q,
                self._reddit.search(sentiment_q, subreddits=None),
                idea_id,
            ))
        else:
            reddit_tasks.append(self._cached_search(
                "reddit_searxng", sentiment_q,
                self._searxng.search(
                    f"site:reddit.com {sentiment_q}",
                    categories="general", language="en", max_results=20,
                ),
                idea_id,
            ))

        phase2 = await asyncio.gather(
            *reddit_tasks,
            self._cached_search(
                "hackernews",
                sentiment_q,
                self._hackernews.search(sentiment_q),
                idea_id,
            ),
            self._cached_search(
                "hackernews_comments",
                sentiment_q,
                self._hackernews.search_comments(sentiment_q),
                idea_id,
            ),
        )

        combined_pre_fallback: list[ResearchResult] = list(phase1) + list(phase2)

        results_after_fallback, _tier = await self._apply_fallback(
            combined_pre_fallback,
            summary,
            progress,
        )

        # --- Phase 3: trend ---
        await self._notify(progress, "trend_analysis")
        trend_key = json.dumps(trend_kw_list, ensure_ascii=False)

        async def fetch_trend() -> TrendRadarResult:
            return await self._safe_trend_analyze(trend_kw_list)

        trend_result = await self._cache.get_cached_trend_or_fetch(
            trend_key, fetch_trend, idea_id=idea_id
        )

        # --- Persist ---
        all_results = list(results_after_fallback)
        fb_i = 0
        for res in results_after_fallback:
            if res.tool_name == "searxng_fallback" and res.success:
                await self._cache_result(
                    idea_id,
                    "searxng_fallback",
                    f"fallback_{fb_i}",
                    res,
                )
                fb_i += 1

        total_sources = await self._save_sources_to_db(idea_id, all_results)
        total_sources += await self._save_trend_sources_to_db(idea_id, trend_result)

        # Graceful empty-handling
        any_success = any(
            r.success
            for r in all_results
            if r.tool_name not in ("research_meta", "research_orchestration")
        )
        trend_ok = trend_result.verdict != TrendVerdict.INSUFFICIENT_DATA or bool(
            trend_result.signals
        )
        if not any_success and not trend_ok:
            logger.error("All research tools failed for idea_id=%s", idea_id)
            warn = ResearchResult(
                tool_name="research_orchestration",
                success=False,
                error_message="Keine Recherche-Quelle lieferte nutzbare Daten.",
                statements=[
                    CitedStatement(
                        text=(
                            "Es konnten keine belastbaren externen Daten abgerufen werden. "
                            "Bitte spaeter erneut versuchen oder Netzwerk/API pruefen."
                        ),
                        statement_type=StatementType.ESTIMATE,
                        confidence=ConfidenceLevel.NO_DATA,
                        sources=[],
                        category="meta",
                    )
                ],
            )
            all_results.append(warn)

        completed_at = datetime.utcnow()
        bundle = ResearchBundle(
            idea_id=idea_id,
            results=all_results,
            trend_radar=trend_result,
            started_at=started_at,
            completed_at=completed_at,
            total_sources=total_sources,
        )
        logger.info(
            "Research finished idea_id=%s sources_saved=%s duration_s=%.2f",
            idea_id,
            total_sources,
            (completed_at - started_at).total_seconds(),
        )
        return bundle

    async def _safe_trend_analyze(self, keywords: list[str]) -> TrendRadarResult:
        try:
            return await self._trend_radar.analyze(
                keywords,
                lookback_quarters=TREND_LOOKBACK_QUARTERS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("TrendRadar.analyze failed: %s", exc)
            return TrendRadarResult(
                verdict=TrendVerdict.INSUFFICIENT_DATA,
                verdict_reasoning=f"Trend-Analyse fehlgeschlagen: {exc}",
            )
