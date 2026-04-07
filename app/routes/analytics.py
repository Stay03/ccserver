from __future__ import annotations

import math

from fastapi import APIRouter, HTTPException, Query

from app.database import get_db

router = APIRouter()

BUCKET_FORMATS = {
    "hour": "%Y-%m-%dT%H:00:00",
    "day": "%Y-%m-%d",
}

LOG_COLUMNS = [
    "request_id", "timestamp", "model",
    "input_tokens", "output_tokens",
    "cache_creation_input_tokens", "cache_read_input_tokens",
    "total_cost_usd", "duration_ms", "duration_api_ms",
    "ttft_ms", "tokens_per_second", "stop_reason",
    "is_error", "is_stream", "num_turns", "origin", "session_id",
]


def _check_db():
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    return db


def _build_where(
    model: str | None,
    origin: str | None,
    since: str | None,
    until: str | None,
    is_stream: bool | None = None,
) -> tuple[str, list]:
    clauses = []
    params = []
    if model:
        clauses.append("model LIKE '%' || ? || '%'")
        params.append(model)
    if origin:
        clauses.append("origin = ?")
        params.append(origin)
    if since:
        clauses.append("timestamp >= ?")
        params.append(since)
    if until:
        clauses.append("timestamp <= ?")
        params.append(until)
    if is_stream is not None:
        clauses.append("is_stream = ?")
        params.append(int(is_stream))
    where = " AND ".join(clauses) if clauses else "1=1"
    return where, params


@router.get("/v1/logs")
async def get_logs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    model: str | None = None,
    origin: str | None = None,
    since: str | None = None,
    until: str | None = None,
    is_stream: bool | None = None,
):
    db = _check_db()
    where, params = _build_where(model, origin, since, until, is_stream)

    count_sql = f"SELECT COUNT(*) FROM request_logs WHERE {where}"
    cursor = await db.execute(count_sql, params)
    row = await cursor.fetchone()
    total = row[0]

    offset = (page - 1) * per_page
    cols = ", ".join(LOG_COLUMNS)
    data_sql = f"SELECT {cols} FROM request_logs WHERE {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    cursor = await db.execute(data_sql, params + [per_page, offset])
    rows = await cursor.fetchall()

    data = []
    for row in rows:
        entry = dict(zip(LOG_COLUMNS, row))
        entry["is_error"] = bool(entry["is_error"])
        entry["is_stream"] = bool(entry["is_stream"])
        data.append(entry)

    return {
        "data": data,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": math.ceil(total / per_page) if total > 0 else 0,
        },
    }


@router.get("/v1/stats")
async def get_stats(
    model: str | None = None,
    origin: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    db = _check_db()
    where, params = _build_where(model, origin, since, until)

    agg_sql = f"""
    SELECT
        COUNT(*) as total_requests,
        COALESCE(SUM(CASE WHEN is_error = 1 THEN 1 ELSE 0 END), 0) as total_errors,
        COALESCE(SUM(total_cost_usd), 0) as total_cost_usd,
        COALESCE(SUM(input_tokens), 0) as total_input_tokens,
        COALESCE(SUM(output_tokens), 0) as total_output_tokens,
        COALESCE(SUM(cache_creation_input_tokens), 0) as total_cache_creation_tokens,
        COALESCE(SUM(cache_read_input_tokens), 0) as total_cache_read_tokens,
        AVG(CASE WHEN is_error = 0 AND tokens_per_second > 0 THEN tokens_per_second END) as avg_tps,
        AVG(CASE WHEN is_error = 0 THEN duration_ms END) as avg_duration_ms,
        AVG(CASE WHEN is_stream = 1 AND ttft_ms IS NOT NULL THEN ttft_ms END) as avg_ttft_ms
    FROM request_logs WHERE {where}
    """
    cursor = await db.execute(agg_sql, params)
    row = await cursor.fetchone()

    total_requests = row[0]

    # Percentiles (non-error rows only)
    pct_where = f"{where} AND is_error = 0" if where != "1=1" else "is_error = 0"
    pct_params = list(params)
    pct_sql = f"SELECT duration_ms FROM request_logs WHERE {pct_where} ORDER BY duration_ms"
    cursor = await db.execute(pct_sql, pct_params)
    durations = [r[0] for r in await cursor.fetchall()]

    p50 = _percentile(durations, 0.50)
    p95 = _percentile(durations, 0.95)
    p99 = _percentile(durations, 0.99)

    # By model breakdown
    model_sql = f"""
    SELECT model, COUNT(*) as requests,
           COALESCE(SUM(total_cost_usd), 0) as cost_usd,
           AVG(CASE WHEN is_error = 0 AND tokens_per_second > 0 THEN tokens_per_second END) as avg_tps
    FROM request_logs WHERE {where}
    GROUP BY model
    """
    cursor = await db.execute(model_sql, params)
    model_rows = await cursor.fetchall()

    by_model = {}
    for mrow in model_rows:
        by_model[mrow[0]] = {
            "requests": mrow[1],
            "cost_usd": round(mrow[2], 6),
            "avg_tps": round(mrow[3], 1) if mrow[3] else 0.0,
        }

    return {
        "total_requests": total_requests,
        "total_errors": row[1],
        "total_cost_usd": round(row[2], 6),
        "total_input_tokens": row[3],
        "total_output_tokens": row[4],
        "total_cache_creation_tokens": row[5],
        "total_cache_read_tokens": row[6],
        "avg_tokens_per_second": round(row[7], 1) if row[7] else 0.0,
        "avg_duration_ms": round(row[8]) if row[8] else 0,
        "avg_ttft_ms": round(row[9]) if row[9] else None,
        "p50_duration_ms": p50,
        "p95_duration_ms": p95,
        "p99_duration_ms": p99,
        "by_model": by_model,
    }


@router.get("/v1/stats/timeseries")
async def get_timeseries(
    bucket: str = Query("day", pattern="^(hour|day)$"),
    model: str | None = None,
    origin: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    db = _check_db()
    where, params = _build_where(model, origin, since, until)
    fmt = BUCKET_FORMATS[bucket]

    ts_sql = f"""
    SELECT
        strftime(?, timestamp) as period,
        COUNT(*) as requests,
        COALESCE(SUM(CASE WHEN is_error = 1 THEN 1 ELSE 0 END), 0) as errors,
        COALESCE(SUM(total_cost_usd), 0) as cost_usd,
        COALESCE(SUM(input_tokens), 0) as input_tokens,
        COALESCE(SUM(output_tokens), 0) as output_tokens,
        AVG(CASE WHEN is_error = 0 AND tokens_per_second > 0 THEN tokens_per_second END) as avg_tps,
        AVG(CASE WHEN is_error = 0 THEN duration_ms END) as avg_duration_ms
    FROM request_logs WHERE {where}
    GROUP BY period
    ORDER BY period
    """
    cursor = await db.execute(ts_sql, [fmt] + params)
    rows = await cursor.fetchall()

    data = []
    for row in rows:
        data.append({
            "period": row[0],
            "requests": row[1],
            "errors": row[2],
            "cost_usd": round(row[3], 6),
            "input_tokens": row[4],
            "output_tokens": row[5],
            "avg_tokens_per_second": round(row[6], 1) if row[6] else 0.0,
            "avg_duration_ms": round(row[7]) if row[7] else 0,
        })

    return {
        "bucket": bucket,
        "data": data,
    }


def _percentile(sorted_values: list, pct: float) -> int | None:
    if not sorted_values:
        return None
    idx = int(len(sorted_values) * pct)
    idx = min(idx, len(sorted_values) - 1)
    return sorted_values[idx]
