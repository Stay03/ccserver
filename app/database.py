from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

from app.config import settings
from app.models.metrics import RequestMetrics

logger = logging.getLogger(__name__)

_db: aiosqlite.Connection | None = None

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS request_logs (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id                  TEXT NOT NULL UNIQUE,
    timestamp                   TEXT NOT NULL,
    model                       TEXT NOT NULL,
    input_tokens                INTEGER DEFAULT 0,
    output_tokens               INTEGER DEFAULT 0,
    cache_creation_input_tokens INTEGER DEFAULT 0,
    cache_read_input_tokens     INTEGER DEFAULT 0,
    total_cost_usd              REAL DEFAULT 0.0,
    duration_ms                 INTEGER DEFAULT 0,
    duration_api_ms             INTEGER DEFAULT 0,
    ttft_ms                     INTEGER,
    tokens_per_second           REAL DEFAULT 0.0,
    stop_reason                 TEXT,
    is_error                    INTEGER DEFAULT 0,
    is_stream                   INTEGER DEFAULT 0,
    num_turns                   INTEGER DEFAULT 1,
    origin                      TEXT DEFAULT 'proxy',
    session_id                  TEXT
);

CREATE INDEX IF NOT EXISTS idx_timestamp ON request_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_model ON request_logs(model);
CREATE INDEX IF NOT EXISTS idx_origin ON request_logs(origin);
"""

_INSERT_SQL = """
INSERT OR IGNORE INTO request_logs (
    request_id, timestamp, model,
    input_tokens, output_tokens,
    cache_creation_input_tokens, cache_read_input_tokens,
    total_cost_usd, duration_ms, duration_api_ms,
    ttft_ms, tokens_per_second, stop_reason,
    is_error, is_stream, num_turns, origin, session_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


async def init_db(db_path: str | None = None) -> None:
    global _db
    path = db_path or str(Path(settings.db_path).resolve())
    _db = await aiosqlite.connect(path)
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.executescript(_CREATE_SQL)
    await _db.commit()
    logger.info("Database initialized at %s", path)


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None
        logger.info("Database closed")


async def insert_request_log(metrics: RequestMetrics) -> None:
    if not _db:
        logger.warning("Database not initialized, skipping metrics insert")
        return
    try:
        await _db.execute(_INSERT_SQL, (
            metrics.request_id,
            metrics.timestamp,
            metrics.model,
            metrics.input_tokens,
            metrics.output_tokens,
            metrics.cache_creation_input_tokens,
            metrics.cache_read_input_tokens,
            metrics.total_cost_usd,
            metrics.duration_ms,
            metrics.duration_api_ms,
            metrics.ttft_ms,
            metrics.tokens_per_second,
            metrics.stop_reason,
            int(metrics.is_error),
            int(metrics.is_stream),
            metrics.num_turns,
            metrics.origin,
            metrics.session_id,
        ))
        await _db.commit()
    except Exception:
        logger.exception("Failed to insert request log")


def get_db() -> aiosqlite.Connection | None:
    return _db
