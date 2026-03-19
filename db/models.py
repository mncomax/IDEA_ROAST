"""SQLite schema definitions and database initialization for Idea Roast."""

from __future__ import annotations

import aiosqlite

CREATE_IDEAS = """
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_chat_id INTEGER NOT NULL,
    raw_idea TEXT,
    persona TEXT,
    current_solution TEXT,
    switch_trigger TEXT,
    monetization TEXT,
    distribution TEXT,
    problem_statement TEXT,
    target_audience TEXT,
    solution TEXT,
    unfair_advantage TEXT,
    status TEXT DEFAULT 'brainstorm',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_RESEARCH_CACHE = """
CREATE TABLE IF NOT EXISTS research_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER REFERENCES ideas(id),
    tool_name TEXT NOT NULL,
    query TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);
"""

CREATE_SOURCES = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER REFERENCES ideas(id),
    url TEXT NOT NULL,
    name TEXT NOT NULL,
    snippet TEXT,
    source_type TEXT,
    confidence TEXT,
    category TEXT,
    extra_json TEXT,
    retrieved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_USER_PROFILES = """
CREATE TABLE IF NOT EXISTS user_profiles (
    telegram_id INTEGER PRIMARY KEY,
    name TEXT,
    skills_json TEXT DEFAULT '[]',
    industries_json TEXT DEFAULT '[]',
    risk_appetite TEXT DEFAULT 'moderate',
    weekly_hours REAL DEFAULT 0,
    preferred_stack_json TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_IDEA_OUTCOMES = """
CREATE TABLE IF NOT EXISTS idea_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER REFERENCES ideas(id),
    outcome TEXT,
    notes TEXT,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_TREND_DATA = """
CREATE TABLE IF NOT EXISTS trend_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER REFERENCES ideas(id),
    signal_source TEXT NOT NULL,
    periods_json TEXT NOT NULL,
    values_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

SCHEMA_STATEMENTS: tuple[str, ...] = (
    CREATE_IDEAS,
    CREATE_RESEARCH_CACHE,
    CREATE_SOURCES,
    CREATE_USER_PROFILES,
    CREATE_IDEA_OUTCOMES,
    CREATE_TREND_DATA,
)


async def init_db(db_path: str) -> None:
    """Create all tables if they do not exist."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        for stmt in SCHEMA_STATEMENTS:
            await db.execute(stmt)
        await db.commit()
