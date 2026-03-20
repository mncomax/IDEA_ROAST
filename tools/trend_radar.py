"""
Trend-Radar: aggregiert Signale (Google Trends, Reddit, HN, News, GitHub) zu einem Urteil.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from aiohttp import ClientTimeout

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from llm.client import LLMClient
from shared.constants import TREND_LOOKBACK_QUARTERS
from shared.exceptions import ResearchError
from shared.types import ConfidenceLevel, Source, TrendRadarResult, TrendSignal, TrendVerdict

logger = logging.getLogger(__name__)

CHART_DIR = "/tmp/idearoast_charts"
HN_ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"
GITHUB_SEARCH = "https://api.github.com/search/repositories"
PULLPUSH_SUBMISSION = "https://api.pullpush.io/reddit/search/submission"
DEFAULT_HTTP_TIMEOUT = ClientTimeout(total=45)

WIKIPEDIA_PAGEVIEWS = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"

_SIGNAL_COLORS: dict[str, str] = {
    "google_trends": "#1f77b4",
    "wikipedia": "#e377c2",
    "reddit": "#ff7f0e",
    "hackernews": "#2ca02c",
    "news": "#d62728",
    "github": "#9467bd",
    "youtube": "#ff0000",
}

_SOURCE_LABELS: dict[str, str] = {
    "google_trends": "Google Trends",
    "wikipedia": "Wikipedia",
    "reddit": "Reddit (SearXNG)",
    "hackernews": "Hacker News",
    "news": "News (SearXNG)",
    "github": "GitHub",
    "youtube": "YouTube (SearXNG)",
}


def _quarter_start_minus_quarters(year: int, month: int, quarters_back: int) -> datetime:
    """month ist Monat eines Quartalstarts (1, 4, 7, 10)."""
    q_idx = (month - 1) // 3
    total = year * 4 + q_idx
    total -= quarters_back
    new_year, rem = divmod(total, 4)
    new_month = rem * 3 + 1
    return datetime(new_year, new_month, 1, tzinfo=timezone.utc)


def _utc_quarters_back(lookback: int) -> list[tuple[str, datetime, datetime]]:
    """Liefert die letzten `lookback` Kalenderquartale (ältestes zuerst), UTC."""
    if lookback < 1:
        return []
    now = datetime.now(timezone.utc)
    q = (now.month - 1) // 3
    newest = datetime(now.year, q * 3 + 1, 1, tzinfo=timezone.utc)
    oldest = _quarter_start_minus_quarters(newest.year, newest.month, lookback - 1)

    periods: list[tuple[str, datetime, datetime]] = []
    y, m = oldest.year, oldest.month
    for _ in range(lookback):
        start = datetime(y, m, 1, tzinfo=timezone.utc)
        label = f"{y}-Q{(m - 1) // 3 + 1}"
        q_end = _quarter_end_exclusive(start)
        periods.append((label, start, q_end))
        if m == 10:
            y += 1
            m = 1
        else:
            m += 3
    return periods


def _quarter_end_exclusive(start: datetime) -> datetime:
    m0 = (start.month - 1) // 3
    next_q_month = m0 * 3 + 4
    if next_q_month > 12:
        return datetime(start.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(start.year, next_q_month, 1, tzinfo=timezone.utc)


def _sanitize_filename_part(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^\w\-]+", "_", text.strip())[:max_len]
    return s or "keywords"


class TrendRadar:
    """Sammelt Trend-Signale parallel und erzeugt Urteil + Chart."""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client
        os.makedirs(CHART_DIR, exist_ok=True)
        self._searxng_base = os.environ.get("SEARXNG_BASE_URL", "http://searxng:8080").rstrip("/")
        self._http: aiohttp.ClientSession | None = None

    async def _get_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=DEFAULT_HTTP_TIMEOUT)
        return self._http

    async def close(self) -> None:
        if self._http is not None and not self._http.closed:
            await self._http.close()
        self._http = None

    async def analyze(
        self,
        keywords: list[str],
        lookback_quarters: int | None = None,
    ) -> TrendRadarResult:
        started = time.perf_counter()
        n = lookback_quarters if lookback_quarters is not None else TREND_LOOKBACK_QUARTERS
        if not keywords or not any(k.strip() for k in keywords):
            raise ResearchError("trend_radar", "Mindestens ein Keyword erforderlich.")

        kw_clean = [k.strip() for k in keywords if k.strip()]
        query = " ".join(kw_clean)

        period_meta = _utc_quarters_back(n)
        canonical_periods = [p[0] for p in period_meta]

        source_order = ["google_trends", "wikipedia", "reddit", "hackernews", "news", "github", "youtube"]
        results = await asyncio.gather(
            self._google_trends_signal(kw_clean, n),
            self._wikipedia_signal(kw_clean, period_meta),
            self._reddit_signal(query, period_meta),
            self._hackernews_signal(query, period_meta),
            self._news_signal(query, period_meta),
            self._github_signal(query, period_meta),
            self._youtube_signal(query, period_meta),
            return_exceptions=True,
        )
        signals: list[TrendSignal] = []
        for src, r in zip(source_order, results):
            if isinstance(r, Exception):
                logger.exception("Trend-Signal %s fehlgeschlagen: %s", src, r)
                signals.append(
                    TrendSignal(
                        source=src,
                        periods=canonical_periods,
                        values=[0.0] * len(canonical_periods),
                        available=False,
                        error_message=str(r)[:500],
                    )
                )
            else:
                signals.append(r)

        signals = [self._align_signal_periods(s, canonical_periods) for s in signals]

        verdict, reasoning = await self._generate_verdict(signals, kw_clean)

        chart_path = ""
        try:
            chart_path = self._generate_chart(signals, kw_clean)
        except Exception as exc:
            logger.exception("Chart-Erstellung fehlgeschlagen: %s", exc)

        sources = self._build_sources(signals, query)
        duration = time.perf_counter() - started
        logger.info(
            "Trend-Radar fertig in %.2fs, verdict=%s, signals_ok=%d/%d",
            duration,
            verdict.value,
            sum(1 for s in signals if s.available),
            len(signals),
        )

        return TrendRadarResult(
            signals=signals,
            verdict=verdict,
            verdict_reasoning=reasoning,
            chart_image_path=chart_path,
            sources=sources,
        )

    def _build_sources(self, signals: list[TrendSignal], query: str) -> list[Source]:
        now = datetime.utcnow()
        out: list[Source] = []
        for sig in signals:
            if not sig.available:
                continue
            label = _SOURCE_LABELS.get(sig.source, sig.source)
            url = (
                f"https://trends.google.com/trends/explore?q={query}"
                if sig.source == "google_trends"
                else "https://github.com/search"
            )
            out.append(
                Source(
                    url=url,
                    name=f"Trend-Radar: {label}",
                    snippet=f"Normalisierte Serie ({len(sig.periods)} Quartale).",
                    retrieved_at=now,
                    source_type=sig.source,
                    extra={"periods": sig.periods[:4], "confidence": ConfidenceLevel.MEDIUM.value},
                )
            )
        return out

    def _align_signal_periods(self, signal: TrendSignal, canonical: list[str]) -> TrendSignal:
        if not canonical:
            return signal
        if not signal.available or not signal.periods:
            return TrendSignal(
                source=signal.source,
                periods=canonical,
                values=[0.0] * len(canonical),
                available=False,
                error_message=signal.error_message or "Keine Daten",
            )
        val_map = dict(zip(signal.periods, signal.values))
        aligned = [float(val_map.get(p, 0.0)) for p in canonical]
        return TrendSignal(
            source=signal.source,
            periods=canonical,
            values=aligned,
            available=signal.available,
            error_message=signal.error_message,
        )

    async def _google_trends_signal(self, keywords: list[str], quarters: int) -> TrendSignal:
        period_meta = _utc_quarters_back(quarters)
        canonical = [p[0] for p in period_meta]

        def _run_pytrends() -> TrendSignal:
            import time as _time

            last_err = ""
            for attempt in range(3):
                if attempt > 0:
                    _time.sleep(2 ** attempt)
                try:
                    return self._pytrends_attempt(keywords, canonical)
                except Exception as exc:
                    last_err = str(exc)[:500]
                    logger.warning("Google Trends attempt %d/3 failed: %s", attempt + 1, last_err)

            return TrendSignal(
                source="google_trends",
                periods=canonical,
                values=[0.0] * len(canonical),
                available=False,
                error_message=f"3 Versuche fehlgeschlagen: {last_err}",
            )

        return await asyncio.to_thread(_run_pytrends)

    def _pytrends_attempt(self, keywords: list[str], canonical: list[str]) -> TrendSignal:
        from pytrends.request import TrendReq

        req = TrendReq(hl="en-US", tz=0, retries=2, backoff_factor=1.0)
        kw = keywords[:5]
        req.build_payload(kw, timeframe="today 24-m")
        iot = req.interest_over_time()
        if iot is None or iot.empty:
            return TrendSignal(
                source="google_trends",
                periods=canonical,
                values=[0.0] * len(canonical),
                available=False,
                error_message="Keine Google-Trends-Zeitreihe",
            )
        value_cols = [c for c in iot.columns if c not in ("isPartial",)]
        if not value_cols:
            return TrendSignal(
                source="google_trends",
                periods=canonical,
                values=[0.0] * len(canonical),
                available=False,
                error_message="Keine Trend-Spalten",
            )
        series = iot[value_cols[0]].astype(float)
        if len(value_cols) > 1:
            series = iot[value_cols].mean(axis=1)

        idx = iot.index
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        else:
            idx = idx.tz_convert("UTC")

        quarter_sums: dict[str, list[float]] = {p: [] for p in canonical}
        for ts, val in zip(idx, series):
            label = f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"
            if label in quarter_sums:
                quarter_sums[label].append(float(val))

        raw_vals = []
        for p in canonical:
            bucket = quarter_sums.get(p, [])
            raw_vals.append(sum(bucket) / len(bucket) if bucket else 0.0)

        mx = max(raw_vals) if raw_vals else 0.0
        if mx <= 0:
            norm = [0.0 for _ in raw_vals]
        else:
            norm = [(v / mx) * 100.0 for v in raw_vals]

        return TrendSignal(
            source="google_trends",
            periods=canonical,
            values=norm,
            available=True,
            error_message="",
        )

    async def _estimate_signal_from_counts(
        self,
        source_name: str,
        period_counts: dict[str, int],
    ) -> TrendSignal:
        periods = sorted(period_counts.keys())
        counts = [int(period_counts[p]) for p in periods]
        if not periods:
            return TrendSignal(
                source=source_name,
                periods=[],
                values=[],
                available=False,
                error_message="Keine Perioden",
            )
        mn, mx = min(counts), max(counts)
        if mx == mn:
            values = [50.0] * len(counts)
        else:
            values = [((c - mn) / (mx - mn)) * 100.0 for c in counts]
        return TrendSignal(
            source=source_name,
            periods=periods,
            values=values,
            available=True,
            error_message="",
        )

    async def _reddit_signal(
        self,
        query: str,
        period_meta: list[tuple[str, datetime, datetime]],
    ) -> TrendSignal:
        """Reddit signal via SearXNG (site:reddit.com) — no API key needed."""
        canonical = [p[0] for p in period_meta]
        counts: dict[str, int] = {label: 0 for label, _, _ in period_meta}
        try:
            session = await self._get_http()
            from urllib.parse import urlencode

            searx_q = f"site:reddit.com {query}"
            for pageno in range(1, 4):
                params = urlencode({
                    "q": searx_q, "format": "json", "categories": "general",
                    "language": "en", "pageno": str(pageno),
                })
                url = f"{self._searxng_base}/search?{params}"
                async with session.get(url) as resp:
                    if resp.status != 200:
                        break
                    data: Any = await resp.json()
                results_raw = data.get("results") if isinstance(data, dict) else None
                if not isinstance(results_raw, list) or not results_raw:
                    break
                for hit in results_raw:
                    if not isinstance(hit, dict):
                        continue
                    pub = hit.get("publishedDate") or hit.get("pubdate")
                    dt = _parse_pub_date(pub)
                    if dt is None:
                        continue
                    for label, start, end in period_meta:
                        if start <= dt < end:
                            counts[label] += 1
                            break
        except Exception as exc:
            logger.warning("Reddit (SearXNG) fehlgeschlagen: %s", exc)
            return TrendSignal(
                source="reddit", periods=canonical,
                values=[0.0] * len(canonical), available=False,
                error_message=str(exc)[:500],
            )

        if sum(counts.values()) == 0:
            return TrendSignal(
                source="reddit", periods=canonical,
                values=[0.0] * len(canonical), available=False,
                error_message="Keine Reddit-Treffer via SearXNG",
            )
        return await self._estimate_signal_from_counts("reddit", counts)

    async def _wikipedia_signal(
        self,
        keywords: list[str],
        period_meta: list[tuple[str, datetime, datetime]],
    ) -> TrendSignal:
        """Wikipedia Pageviews API — free, reliable, measures real search interest."""
        canonical = [p[0] for p in period_meta]
        counts: dict[str, int] = {label: 0 for label, _, _ in period_meta}
        session = await self._get_http()
        headers = {"User-Agent": "IdeaRoast-TrendRadar/1.0 (contact: bot@idearoast.dev)"}

        for kw in keywords[:3]:
            title = kw.strip().replace(" ", "_")
            if not title:
                continue
            start_dt = period_meta[0][1]
            end_dt = period_meta[-1][2] - timedelta(days=1)
            start_str = start_dt.strftime("%Y%m%d")
            end_str = end_dt.strftime("%Y%m%d")
            url = (
                f"{WIKIPEDIA_PAGEVIEWS}/en.wikipedia/all-access/all-agents"
                f"/{title}/monthly/{start_str}/{end_str}"
            )
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        continue
                    data: Any = await resp.json()
                items = data.get("items") if isinstance(data, dict) else None
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    ts_str = item.get("timestamp", "")
                    views = item.get("views", 0)
                    if not ts_str or not isinstance(views, (int, float)):
                        continue
                    try:
                        dt = datetime.strptime(str(ts_str)[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
                    for label, start, end in period_meta:
                        if start <= dt < end:
                            counts[label] += int(views)
                            break
            except Exception as exc:
                logger.debug("Wikipedia pageviews for %r failed: %s", title, exc)

        if sum(counts.values()) == 0:
            return TrendSignal(
                source="wikipedia", periods=canonical,
                values=[0.0] * len(canonical), available=False,
                error_message="Keine Wikipedia-Pageviews gefunden",
            )
        return await self._estimate_signal_from_counts("wikipedia", counts)

    async def _youtube_signal(
        self,
        query: str,
        period_meta: list[tuple[str, datetime, datetime]],
    ) -> TrendSignal:
        """YouTube video count via SearXNG — measures content creation interest."""
        canonical = [p[0] for p in period_meta]
        counts: dict[str, int] = {label: 0 for label, _, _ in period_meta}
        try:
            session = await self._get_http()
            from urllib.parse import urlencode

            for pageno in range(1, 3):
                params = urlencode({
                    "q": query, "format": "json", "categories": "videos",
                    "language": "en", "pageno": str(pageno),
                })
                url = f"{self._searxng_base}/search?{params}"
                async with session.get(url) as resp:
                    if resp.status != 200:
                        break
                    data: Any = await resp.json()
                results_raw = data.get("results") if isinstance(data, dict) else None
                if not isinstance(results_raw, list) or not results_raw:
                    break
                for hit in results_raw:
                    if not isinstance(hit, dict):
                        continue
                    pub = hit.get("publishedDate") or hit.get("pubdate")
                    dt = _parse_pub_date(pub)
                    if dt is None:
                        continue
                    for label, start, end in period_meta:
                        if start <= dt < end:
                            counts[label] += 1
                            break
        except Exception as exc:
            logger.warning("YouTube (SearXNG) fehlgeschlagen: %s", exc)
            return TrendSignal(
                source="youtube", periods=canonical,
                values=[0.0] * len(canonical), available=False,
                error_message=str(exc)[:500],
            )

        if sum(counts.values()) == 0:
            return TrendSignal(
                source="youtube", periods=canonical,
                values=[0.0] * len(canonical), available=False,
                error_message="Keine YouTube-Videos via SearXNG",
            )
        return await self._estimate_signal_from_counts("youtube", counts)

    async def _hackernews_signal(
        self,
        query: str,
        period_meta: list[tuple[str, datetime, datetime]],
    ) -> TrendSignal:
        canonical = [p[0] for p in period_meta]
        counts: dict[str, int] = {}
        try:
            session = await self._get_http()
            for label, start, end in period_meta:
                lo = int(start.timestamp())
                hi = int(end.timestamp())
                params: dict[str, str | int] = {
                    "query": query,
                    "tags": "story",
                    "hitsPerPage": 1,
                    "numericFilters": f"created_at_i>{lo},created_at_i<{hi}",
                }
                async with session.get(HN_ALGOLIA_SEARCH, params=params) as resp:
                    if resp.status != 200:
                        counts[label] = 0
                        continue
                    payload: Any = await resp.json()
                nb = payload.get("nbHits") if isinstance(payload, dict) else None
                counts[label] = int(nb) if isinstance(nb, (int, float)) else 0
        except Exception as exc:
            logger.warning("Hacker News fehlgeschlagen: %s", exc)
            return TrendSignal(
                source="hackernews",
                periods=canonical,
                values=[0.0] * len(canonical),
                available=False,
                error_message=str(exc)[:500],
            )

        if sum(counts.values()) == 0:
            return TrendSignal(
                source="hackernews",
                periods=canonical,
                values=[0.0] * len(canonical),
                available=False,
                error_message="Keine HN-Treffer im Zeitraum",
            )
        return await self._estimate_signal_from_counts("hackernews", counts)

    async def _news_signal(
        self,
        query: str,
        period_meta: list[tuple[str, datetime, datetime]],
    ) -> TrendSignal:
        canonical = [p[0] for p in period_meta]
        counts: dict[str, int] = {label: 0 for label, _, _ in period_meta}
        try:
            session = await self._get_http()
            seen_urls: set[str] = set()
            for pageno in range(1, 4):
                url = f"{self._searxng_base}/search?{self._encode_searx_params(query, pageno)}"
                async with session.get(url) as resp:
                    if resp.status != 200:
                        break
                    data: Any = await resp.json()
                results_raw = data.get("results") if isinstance(data, dict) else None
                if not isinstance(results_raw, list):
                    break
                for hit in results_raw:
                    if not isinstance(hit, dict):
                        continue
                    link = hit.get("url") or hit.get("link") or ""
                    if isinstance(link, str) and link in seen_urls:
                        continue
                    if isinstance(link, str) and link:
                        seen_urls.add(link)
                    pub = hit.get("publishedDate") or hit.get("pubdate")
                    dt = _parse_pub_date(pub)
                    if dt is None:
                        continue
                    for label, start, end in period_meta:
                        if start <= dt < end:
                            counts[label] += 1
                            break
        except Exception as exc:
            logger.warning("News (SearXNG) fehlgeschlagen: %s", exc)
            return TrendSignal(
                source="news",
                periods=canonical,
                values=[0.0] * len(canonical),
                available=False,
                error_message=str(exc)[:500],
            )

        if sum(counts.values()) == 0:
            return TrendSignal(
                source="news",
                periods=canonical,
                values=[0.0] * len(canonical),
                available=False,
                error_message="Keine News mit Datum oder SearXNG leer",
            )
        return await self._estimate_signal_from_counts("news", counts)

    def _encode_searx_params(self, query: str, pageno: int = 1) -> str:
        from urllib.parse import urlencode

        return urlencode(
            {
                "q": query,
                "format": "json",
                "categories": "news",
                "language": "de",
                "pageno": str(pageno),
            }
        )

    async def _github_signal(
        self,
        query: str,
        period_meta: list[tuple[str, datetime, datetime]],
    ) -> TrendSignal:
        canonical = [p[0] for p in period_meta]
        counts: dict[str, int] = {}
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "IdeaRoast-TrendRadar/1.0",
        }
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            session = await self._get_http()
            for label, start, end in period_meta:
                d0 = start.date().isoformat()
                d1 = (end - timedelta(days=1)).date().isoformat()
                q = f"{query} created:{d0}..{d1}"
                params = {"q": q, "per_page": 1}
                async with session.get(GITHUB_SEARCH, params=params, headers=headers) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.debug("GitHub HTTP %s: %s", resp.status, text[:200])
                        counts[label] = 0
                        continue
                    payload: Any = await resp.json()
                tc = payload.get("total_count") if isinstance(payload, dict) else None
                counts[label] = int(tc) if isinstance(tc, (int, float)) else 0
                await asyncio.sleep(0.15)
        except Exception as exc:
            logger.warning("GitHub-Suche fehlgeschlagen: %s", exc)
            return TrendSignal(
                source="github",
                periods=canonical,
                values=[0.0] * len(canonical),
                available=False,
                error_message=str(exc)[:500],
            )

        if sum(counts.values()) == 0:
            return TrendSignal(
                source="github",
                periods=canonical,
                values=[0.0] * len(canonical),
                available=False,
                error_message="Keine GitHub-Repos im Zeitraum",
            )
        return await self._estimate_signal_from_counts("github", counts)

    def _generate_chart(self, signals: list[TrendSignal], keywords: list[str]) -> str:
        try:
            plt.style.use("seaborn-v0_8-whitegrid")
        except OSError:
            plt.style.use("ggplot")

        available = [s for s in signals if s.available and s.periods and s.values]
        ts = int(time.time())
        safe = _sanitize_filename_part("_".join(keywords[:3]))
        out_path = os.path.join(CHART_DIR, f"{safe}_{ts}.png")

        fig, ax = plt.subplots(figsize=(10, 5.5), dpi=120)
        title = f"Trend-Radar: {', '.join(keywords)}"
        ax.set_title(title, fontsize=13, fontweight="bold")
        ax.set_xlabel("Quartal")
        ax.set_ylabel("Normalisiert (0–100)")
        ax.set_ylim(0, 105)

        if not available:
            ax.text(0.5, 0.5, "Keine verfügbaren Signale", ha="center", va="center", transform=ax.transAxes)
        else:
            x_labels = available[0].periods
            x = range(len(x_labels))
            for sig in available:
                color = _SIGNAL_COLORS.get(sig.source, "#333333")
                label = _SOURCE_LABELS.get(sig.source, sig.source)
                ax.plot(x, sig.values, marker="o", linewidth=2, label=label, color=color, markersize=4)
            ax.set_xticks(list(x))
            ax.set_xticklabels(x_labels, rotation=35, ha="right")
            ax.legend(loc="upper left", framealpha=0.95)

        fig.tight_layout()
        fig.savefig(out_path, bbox_inches="tight")
        plt.close(fig)
        logger.info("Chart gespeichert: %s", out_path)
        return out_path

    def _rule_based_verdict(
        self,
        signals: list[TrendSignal],
    ) -> tuple[TrendVerdict, str]:
        avail = [s for s in signals if s.available and s.values]
        if len(avail) < 2:
            return TrendVerdict.INSUFFICIENT_DATA, (
                "Zu wenige unabhängige Signale (mindestens 2 erforderlich). "
                "Bitte später erneut versuchen oder andere Keywords wählen."
            )

        n = len(avail[0].values)
        if n < 4:
            return TrendVerdict.INSUFFICIENT_DATA, (
                "Die Zeitreihe ist zu kurz für eine belastbare Trend-Aussage (mindestens 4 Quartale)."
            )

        combined: list[float] = []
        for i in range(n):
            vals = [s.values[i] for s in avail if i < len(s.values)]
            combined.append(sum(vals) / len(vals))

        first2 = sum(combined[:2]) / 2.0
        last2 = sum(combined[-2:]) / 2.0
        overall_mean = sum(combined) / len(combined)

        if first2 <= 1e-6:
            rel_change = 100.0 if last2 > 1.0 else 0.0
        else:
            rel_change = ((last2 - first2) / first2) * 100.0

        spike = False
        if n >= 4:
            prev_mean = sum(combined[-4:-1]) / 3.0
            if prev_mean > 1e-6 and combined[-1] > 1.4 * prev_mean:
                spike = True

        if overall_mean < 25.0 and rel_change > 8.0:
            return TrendVerdict.EARLY, (
                f"Die Signale liegen insgesamt noch auf niedrigem Niveau (Mittelwert ~{overall_mean:.0f}/100), "
                f"steigen aber gegenüber dem Frühzeitfenster um etwa {rel_change:.0f} % — "
                "passend für eine frühe Nische oder ein aufkommendes Thema."
            )

        if rel_change > 30.0:
            return TrendVerdict.RISING, (
                f"Der gemittelte Trend steigt stark: die letzten zwei Quartale liegen etwa "
                f"{rel_change:.0f} % über dem frühen Referenzfenster — insgesamt aufwärtsgerichtet."
            )

        if rel_change > 15.0 and spike:
            return TrendVerdict.HYPE_PEAK, (
                "Es gibt ein starkes kurzfristiges Hoch (Spike im letzten Quartal) bei gleichzeitig "
                f"deutlichem Plus (~{rel_change:.0f} %) — möglicher Hype-Peak oder viraler Schub."
            )

        if -15.0 <= rel_change <= 15.0:
            return TrendVerdict.PLATEAU, (
                f"Die Entwicklung bewegt sich in einem Band von etwa {rel_change:+.0f} % "
                "zwischen Früh- und Spätphase — eher seitwärts / stabil."
            )

        if rel_change < -15.0:
            return TrendVerdict.DECLINING, (
                f"Die aggregierte Kurve fällt gegenüber dem Anfangszeitfenster um etwa {abs(rel_change):.0f} % — "
                "nachlassendes Interesse oder Reifephase."
            )

        return TrendVerdict.PLATEAU, (
            f"Kein eindeutiger Extremfall (Veränderung ca. {rel_change:+.0f} %); Einstufung als Plateau."
        )

    async def _generate_verdict(
        self,
        signals: list[TrendSignal],
        keywords: list[str],
    ) -> tuple[TrendVerdict, str]:
        rule_v, rule_reason = self._rule_based_verdict(signals)

        if self._llm_client is None:
            return rule_v, rule_reason

        try:
            payload = {
                "keywords": keywords,
                "signals": [
                    {
                        "source": s.source,
                        "available": s.available,
                        "periods": s.periods,
                        "values": [round(v, 2) for v in s.values],
                        "error": s.error_message,
                    }
                    for s in signals
                ],
                "rule_suggestion": {"verdict": rule_v.value, "reasoning": rule_reason},
            }
            system_prompt = (
                "Du bist ein Analyst für Markt- und Techniktrends. "
                "Antworte NUR mit einem JSON-Objekt mit genau zwei Schlüsseln: "
                '"verdict" (einer von: rising, plateau, declining, early, hype_peak, insufficient_data) '
                'und "reasoning" (kurze Begründung auf Deutsch, 2-4 Sätze). '
                "Nutze die Signalserien und die Regel-Empfehlung als Orientierung, "
                "kannst aber abweichen wenn die Daten es plausibel machen."
            )
            user_message = json.dumps(payload, ensure_ascii=False)
            raw = await self._llm_client.complete(
                system_prompt=system_prompt,
                user_message=user_message,
                task="analysis",
            )
            parsed = _extract_json_object(raw)
            v_str = str(parsed.get("verdict", "")).lower().strip()
            reasoning = str(parsed.get("reasoning", rule_reason)).strip()
            mapped = _parse_verdict_enum(v_str)
            if mapped is None:
                logger.warning("LLM-Verdict nicht erkannt: %s, nutze Regelwerk", v_str)
                return rule_v, rule_reason
            return mapped, reasoning or rule_reason
        except Exception as exc:
            logger.warning("LLM-Verdict fehlgeschlagen, nutze Regelwerk: %s", exc)
            return rule_v, rule_reason


def _parse_verdict_enum(raw: str) -> TrendVerdict | None:
    mapping = {
        "rising": TrendVerdict.RISING,
        "plateau": TrendVerdict.PLATEAU,
        "declining": TrendVerdict.DECLINING,
        "early": TrendVerdict.EARLY,
        "hype_peak": TrendVerdict.HYPE_PEAK,
        "hypepeak": TrendVerdict.HYPE_PEAK,
        "insufficient_data": TrendVerdict.INSUFFICIENT_DATA,
    }
    return mapping.get(raw)


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    return json.loads(cleaned)


def _parse_pub_date(pub: Any) -> datetime | None:
    if pub is None:
        return None
    if isinstance(pub, datetime):
        return pub if pub.tzinfo else pub.replace(tzinfo=timezone.utc)
    s = str(pub).strip()
    if not s:
        return None
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None
