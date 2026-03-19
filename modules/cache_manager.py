"""
Research result cache: read-through lookup before external API calls, TTL via DB.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable

from db.repository import Repository
from shared.constants import RESEARCH_CACHE_TTL
from shared.types import (
    CitedStatement,
    ConfidenceLevel,
    ResearchResult,
    Source,
    StatementType,
    TrendRadarResult,
    TrendSignal,
    TrendVerdict,
)

logger = logging.getLogger(__name__)

FetchCoro = Callable[[], Awaitable[ResearchResult]]
TrendFetchCoro = Callable[[], Awaitable[TrendRadarResult]]


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


def research_result_to_cache_payload(result: ResearchResult) -> dict[str, Any]:
    return _sanitize_for_json(asdict(result))


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.utcnow()


def _source_from_dict(d: dict[str, Any]) -> Source:
    return Source(
        url=str(d.get("url", "")),
        name=str(d.get("name", "")),
        snippet=str(d.get("snippet") or ""),
        retrieved_at=_parse_dt(d.get("retrieved_at")),
        source_type=str(d.get("source_type") or ""),
        extra=d.get("extra") if isinstance(d.get("extra"), dict) else {},
    )


def _statement_type_from_raw(raw: Any) -> StatementType:
    if isinstance(raw, StatementType):
        return raw
    if isinstance(raw, str):
        try:
            return StatementType(raw)
        except ValueError:
            pass
    return StatementType.ESTIMATE


def _confidence_from_raw(raw: Any) -> ConfidenceLevel:
    if isinstance(raw, ConfidenceLevel):
        return raw
    if isinstance(raw, str):
        try:
            return ConfidenceLevel(raw)
        except ValueError:
            pass
    return ConfidenceLevel.MEDIUM


def _cited_statement_from_dict(d: dict[str, Any]) -> CitedStatement:
    sources_raw = d.get("sources") or []
    sources = [_source_from_dict(s) for s in sources_raw if isinstance(s, dict)]
    return CitedStatement(
        text=str(d.get("text") or ""),
        statement_type=_statement_type_from_raw(d.get("statement_type")),
        confidence=_confidence_from_raw(d.get("confidence")),
        sources=sources,
        category=str(d.get("category") or ""),
    )


def research_result_from_dict(data: dict[str, Any], tool_name: str | None = None) -> ResearchResult:
    statements_raw = data.get("statements") or []
    statements = [
        _cited_statement_from_dict(s) for s in statements_raw if isinstance(s, dict)
    ]
    raw_data = data.get("raw_data")
    if not isinstance(raw_data, dict):
        raw_data = {}
    res = ResearchResult(
        tool_name=str(data.get("tool_name") or tool_name or ""),
        statements=statements,
        raw_data=raw_data,
        success=bool(data.get("success", True)),
        error_message=str(data.get("error_message") or ""),
        duration_seconds=float(data.get("duration_seconds") or 0.0),
    )
    if tool_name:
        res.tool_name = tool_name
    return res


def trend_radar_from_cache_payload(data: dict[str, Any]) -> TrendRadarResult:
    signals_out: list[TrendSignal] = []
    for s in data.get("signals") or []:
        if not isinstance(s, dict):
            continue
        signals_out.append(
            TrendSignal(
                source=str(s.get("source") or ""),
                periods=[str(x) for x in (s.get("periods") or [])],
                values=[float(x) for x in (s.get("values") or [])],
                available=bool(s.get("available", True)),
                error_message=str(s.get("error_message") or ""),
            )
        )
    verdict_raw = data.get("verdict")
    if isinstance(verdict_raw, TrendVerdict):
        verdict = verdict_raw
    elif isinstance(verdict_raw, str):
        try:
            verdict = TrendVerdict(verdict_raw)
        except ValueError:
            verdict = TrendVerdict.INSUFFICIENT_DATA
    else:
        verdict = TrendVerdict.INSUFFICIENT_DATA
    sources_raw = data.get("sources") or []
    sources = [_source_from_dict(s) for s in sources_raw if isinstance(s, dict)]
    return TrendRadarResult(
        signals=signals_out,
        verdict=verdict,
        verdict_reasoning=str(data.get("verdict_reasoning") or ""),
        chart_image_path=str(data.get("chart_image_path") or ""),
        sources=sources,
    )


class CacheManager:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    async def get_cached_or_fetch(
        self,
        tool_name: str,
        query: str,
        fetch_coro: FetchCoro,
        idea_id: int | None = None,
        ttl: int = RESEARCH_CACHE_TTL,
    ) -> ResearchResult:
        cached = await self._repo.get_research_cache(tool_name, query)
        if cached is not None:
            payload = cached.get("result_json")
            if isinstance(payload, dict):
                result = research_result_from_dict(payload, tool_name=tool_name)
                logger.info(
                    "Research cache HIT tool=%s query_len=%s",
                    tool_name,
                    len(query),
                )
                return result

        result = await fetch_coro()
        result.tool_name = tool_name
        if not result.success:
            return result
        try:
            payload = research_result_to_cache_payload(result)
            await self._repo.save_research_cache(
                idea_id,
                tool_name,
                query,
                payload,
                ttl,
            )
        except Exception:
            logger.exception("save_research_cache failed tool=%s", tool_name)
        return result

    async def get_cached_trend_or_fetch(
        self,
        query: str,
        fetch_coro: TrendFetchCoro,
        idea_id: int | None = None,
        ttl: int = RESEARCH_CACHE_TTL,
    ) -> TrendRadarResult:
        cached = await self._repo.get_research_cache("trend_radar", query)
        if cached is not None:
            payload = cached.get("result_json")
            if isinstance(payload, dict):
                logger.info("Research cache HIT tool=trend_radar query_len=%s", len(query))
                return trend_radar_from_cache_payload(payload)

        trend = await fetch_coro()
        try:
            payload = _sanitize_for_json(asdict(trend))
            await self._repo.save_research_cache(
                idea_id,
                "trend_radar",
                query,
                payload,
                ttl,
            )
        except Exception:
            logger.exception("save_research_cache failed for trend_radar")
        return trend

    async def invalidate(self, tool_name: str, query: str) -> None:
        await self._repo.delete_research_cache(tool_name, query)

    async def get_cache_stats(self) -> dict[str, Any]:
        raw = await self._repo.get_research_cache_stats()
        total = raw.get("total_entries", 0)
        expired = raw.get("expired_entries", 0)
        bytes_len = raw.get("result_bytes", 0)
        active = max(0, total - expired)
        return {
            "total_entries": total,
            "expired_entries": expired,
            "active_entries": active,
            "estimated_size_bytes": bytes_len,
        }


__all__ = [
    "CacheManager",
    "research_result_to_cache_payload",
    "research_result_from_dict",
    "trend_radar_from_cache_payload",
]
