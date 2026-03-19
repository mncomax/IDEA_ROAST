"""Async data access layer using aiosqlite."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

_IDEA_UPDATE_COLUMNS = frozenset(
    {
        "telegram_chat_id",
        "raw_idea",
        "persona",
        "current_solution",
        "switch_trigger",
        "monetization",
        "distribution",
        "problem_statement",
        "target_audience",
        "solution",
        "unfair_advantage",
        "status",
    }
)

_PROFILE_COLUMNS = frozenset(
    {
        "name",
        "skills_json",
        "industries_json",
        "risk_appetite",
        "weekly_hours",
        "preferred_stack_json",
    }
)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads_maybe(raw: str | None, default: Any = None) -> Any:
    if raw is None or raw == "":
        return default
    return json.loads(raw)


class Repository:
    VALIDATION_SNAPSHOT_TOOL = "validation_snapshot"

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self._conn is not None:
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON;")

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Repository is not connected; call connect() first")
        return self._conn

    async def create_idea(self, telegram_chat_id: int, raw_idea: str | None) -> int:
        db = self._require_conn()
        cur = await db.execute(
            """
            INSERT INTO ideas (telegram_chat_id, raw_idea)
            VALUES (?, ?)
            """,
            (telegram_chat_id, raw_idea),
        )
        await db.commit()
        return int(cur.lastrowid)

    async def update_idea(self, idea_id: int, **fields: Any) -> None:
        db = self._require_conn()
        updates = {k: v for k, v in fields.items() if k in _IDEA_UPDATE_COLUMNS}
        if not updates:
            return
        cols = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values())
        values.append(idea_id)
        await db.execute(
            f"UPDATE ideas SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values,
        )
        await db.commit()

    async def get_idea(self, idea_id: int) -> dict[str, Any] | None:
        db = self._require_conn()
        cur = await db.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        return dict(row)

    async def get_ideas_by_chat(
        self, telegram_chat_id: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        db = self._require_conn()
        cur = await db.execute(
            """
            SELECT * FROM ideas
            WHERE telegram_chat_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (telegram_chat_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def save_source(self, idea_id: int, source_data: dict[str, Any]) -> int:
        db = self._require_conn()
        extra = source_data.get("extra_json")
        extra_str: str | None
        if extra is None:
            extra_str = None
        elif isinstance(extra, str):
            extra_str = extra
        else:
            extra_str = _json_dumps(extra)
        cur = await db.execute(
            """
            INSERT INTO sources (
                idea_id, url, name, snippet, source_type,
                confidence, category, extra_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                idea_id,
                source_data["url"],
                source_data["name"],
                source_data.get("snippet"),
                source_data.get("source_type"),
                source_data.get("confidence"),
                source_data.get("category"),
                extra_str,
            ),
        )
        await db.commit()
        return int(cur.lastrowid)

    async def get_sources_by_idea(
        self, idea_id: int, category: str | None = None
    ) -> list[dict[str, Any]]:
        db = self._require_conn()
        if category is None:
            cur = await db.execute(
                "SELECT * FROM sources WHERE idea_id = ? ORDER BY retrieved_at DESC",
                (idea_id,),
            )
        else:
            cur = await db.execute(
                """
                SELECT * FROM sources
                WHERE idea_id = ? AND category = ?
                ORDER BY retrieved_at DESC
                """,
                (idea_id, category),
            )
        rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if d.get("extra_json") is not None:
                d["extra_json"] = _json_loads_maybe(d["extra_json"], default=None)
            out.append(d)
        return out

    async def save_research_cache(
        self,
        idea_id: int | None,
        tool_name: str,
        query: str,
        result_json: str | dict[str, Any] | list[Any],
        ttl_seconds: int,
    ) -> int:
        db = self._require_conn()
        if isinstance(result_json, (dict, list)):
            payload = _json_dumps(result_json)
        else:
            payload = result_json
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).strftime("%Y-%m-%d %H:%M:%S")
        cur = await db.execute(
            """
            INSERT INTO research_cache (
                idea_id, tool_name, query, result_json, expires_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (idea_id, tool_name, query, payload, expires_at),
        )
        await db.commit()
        return int(cur.lastrowid)

    async def get_research_cache(
        self, tool_name: str, query: str
    ) -> dict[str, Any] | None:
        db = self._require_conn()
        cur = await db.execute(
            """
            SELECT * FROM research_cache
            WHERE tool_name = ? AND query = ?
              AND datetime(expires_at) > datetime('now')
            ORDER BY id DESC
            LIMIT 1
            """,
            (tool_name, query),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["result_json"] = _json_loads_maybe(d["result_json"], default=None)
        return d

    async def delete_research_cache(self, tool_name: str, query: str) -> None:
        db = self._require_conn()
        await db.execute(
            "DELETE FROM research_cache WHERE tool_name = ? AND query = ?",
            (tool_name, query),
        )
        await db.commit()

    async def get_research_cache_stats(self) -> dict[str, Any]:
        """Counts all rows and expired rows; sums JSON payload sizes (UTF-8 byte length)."""
        db = self._require_conn()
        cur = await db.execute(
            """
            SELECT
              COUNT(*) AS total_entries,
              SUM(CASE WHEN datetime(expires_at) <= datetime('now') THEN 1 ELSE 0 END) AS expired_entries,
              COALESCE(SUM(LENGTH(result_json)), 0) AS result_bytes
            FROM research_cache
            """
        )
        row = await cur.fetchone()
        if row is None:
            return {
                "total_entries": 0,
                "expired_entries": 0,
                "result_bytes": 0,
            }
        d = dict(row)
        return {
            "total_entries": int(d.get("total_entries") or 0),
            "expired_entries": int(d.get("expired_entries") or 0),
            "result_bytes": int(d.get("result_bytes") or 0),
        }

    async def save_or_update_profile(self, telegram_id: int, **fields: Any) -> None:
        db = self._require_conn()
        data = {k: v for k, v in fields.items() if k in _PROFILE_COLUMNS}
        for key in ("skills_json", "industries_json", "preferred_stack_json"):
            if key in data and not isinstance(data[key], str):
                data[key] = _json_dumps(data[key])
        cur = await db.execute(
            "SELECT telegram_id FROM user_profiles WHERE telegram_id = ?",
            (telegram_id,),
        )
        exists = await cur.fetchone() is not None
        if not exists:
            cols = ["telegram_id"] + list(data.keys())
            placeholders = ", ".join("?" * len(cols))
            col_names = ", ".join(cols)
            await db.execute(
                f"INSERT INTO user_profiles ({col_names}) VALUES ({placeholders})",
                [telegram_id] + list(data.values()),
            )
        else:
            if not data:
                return
            sets = ", ".join(f"{k} = ?" for k in data)
            values = list(data.values()) + [telegram_id]
            await db.execute(
                f"""
                UPDATE user_profiles
                SET {sets}, updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = ?
                """,
                values,
            )
        await db.commit()

    async def get_profile(self, telegram_id: int) -> dict[str, Any] | None:
        db = self._require_conn()
        cur = await db.execute(
            "SELECT * FROM user_profiles WHERE telegram_id = ?", (telegram_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        for key in ("skills_json", "industries_json", "preferred_stack_json"):
            if key in d:
                d[key] = _json_loads_maybe(d[key], default=[])
        return d

    async def save_outcome(
        self, idea_id: int, outcome: str | None, notes: str | None
    ) -> int:
        db = self._require_conn()
        cur = await db.execute(
            """
            INSERT INTO idea_outcomes (idea_id, outcome, notes)
            VALUES (?, ?, ?)
            """,
            (idea_id, outcome, notes),
        )
        await db.commit()
        return int(cur.lastrowid)

    async def get_outcomes_for_idea(self, idea_id: int) -> list[dict[str, Any]]:
        db = self._require_conn()
        cur = await db.execute(
            """
            SELECT * FROM idea_outcomes
            WHERE idea_id = ?
            ORDER BY recorded_at DESC
            """,
            (idea_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def save_validation_snapshot(self, idea_id: int, snapshot: dict[str, Any]) -> int:
        """Persist scoring + recommendation for history/detail views (research_cache)."""
        return await self.save_research_cache(
            idea_id,
            self.VALIDATION_SNAPSHOT_TOOL,
            f"idea:{idea_id}",
            snapshot,
            ttl_seconds=86400 * 365 * 10,
        )

    async def get_validation_snapshot(self, idea_id: int) -> dict[str, Any] | None:
        db = self._require_conn()
        cur = await db.execute(
            """
            SELECT result_json FROM research_cache
            WHERE idea_id = ?
              AND tool_name = ?
              AND datetime(expires_at) > datetime('now')
            ORDER BY id DESC
            LIMIT 1
            """,
            (idea_id, self.VALIDATION_SNAPSHOT_TOOL),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return _json_loads_maybe(row["result_json"], default=None)

    async def save_trend_data(
        self,
        idea_id: int,
        signal_source: str,
        periods: list[Any],
        values: list[Any],
    ) -> int:
        db = self._require_conn()
        cur = await db.execute(
            """
            INSERT INTO trend_data (idea_id, signal_source, periods_json, values_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                idea_id,
                signal_source,
                _json_dumps(periods),
                _json_dumps(values),
            ),
        )
        await db.commit()
        return int(cur.lastrowid)

    async def get_trend_data(self, idea_id: int) -> list[dict[str, Any]]:
        db = self._require_conn()
        cur = await db.execute(
            """
            SELECT * FROM trend_data
            WHERE idea_id = ?
            ORDER BY created_at ASC
            """,
            (idea_id,),
        )
        rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["periods_json"] = _json_loads_maybe(d["periods_json"], default=None)
            d["values_json"] = _json_loads_maybe(d["values_json"], default=None)
            out.append(d)
        return out

    async def backup(self, backup_dir: str) -> Path:
        dest_dir = Path(backup_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        src = Path(self.db_path)
        dest = dest_dir / f"{src.stem}_{stamp}{src.suffix}"
        await asyncio.to_thread(shutil.copy2, self.db_path, dest)
        return dest
