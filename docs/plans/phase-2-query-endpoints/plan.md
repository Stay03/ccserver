# Phase 2: Query Endpoints (Updated with all fixes)

## Goal
Three GET endpoints to expose logged data — paginated history, aggregate stats, and timeseries for charts.

## Depends on
Phase 1 (data must be in SQLite first)

## Files to Create

### `app/routes/analytics.py`

All three endpoints on a new `APIRouter`. Each endpoint:
- Checks `get_db()` first → 503 if None (Issue 12)
- Uses parameterized queries only (Issue 10)
- Uses `COALESCE` for all aggregates (Issue 11)

---

#### `GET /v1/logs` — Paginated request history

**Params:** `page` (default 1), `per_page` (default 50, max 200), `model`, `origin`, `since` (ISO), `until` (ISO), `is_stream` (bool, optional)

**Filters:**
- `model` uses `LIKE '%' || ? || '%'` so `?model=sonnet` matches `claude-sonnet-4-6` (Issue 2)
- `is_stream` filter added (Issue 9)
- All filters are optional, parameterized

**Response includes all useful DB columns (Issue 3):**
```json
{
  "data": [
    {
      "request_id": "msg_xxx",
      "timestamp": "2026-04-07T12:00:00+00:00",
      "model": "claude-sonnet-4-6",
      "input_tokens": 3,
      "output_tokens": 12,
      "cache_creation_input_tokens": 4552,
      "cache_read_input_tokens": 11027,
      "total_cost_usd": 0.020567,
      "duration_ms": 1517,
      "duration_api_ms": 1421,
      "ttft_ms": null,
      "tokens_per_second": 8.44,
      "stop_reason": "end_turn",
      "is_error": false,
      "is_stream": false,
      "num_turns": 1,
      "origin": "proxy",
      "session_id": "50f85a80-..."
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 50,
    "total": 342,
    "total_pages": 7
  }
}
```

**SQL:**
```sql
SELECT request_id, timestamp, model,
       input_tokens, output_tokens,
       cache_creation_input_tokens, cache_read_input_tokens,
       total_cost_usd, duration_ms, duration_api_ms,
       ttft_ms, tokens_per_second, stop_reason,
       is_error, is_stream, num_turns, origin, session_id
FROM request_logs
WHERE 1=1
  [AND model LIKE '%' || ? || '%']
  [AND origin = ?]
  [AND is_stream = ?]
  [AND timestamp >= ?]
  [AND timestamp <= ?]
ORDER BY timestamp DESC
LIMIT ? OFFSET ?
```

Plus `SELECT COUNT(*) ...` with same filters for pagination.

---

#### `GET /v1/stats` — Aggregate metrics

**Params:** `model`, `origin`, `since`, `until`

**Key fixes:**
- `avg_tokens_per_second` excludes errors and zeros (Issue 6)
- `avg_ttft_ms` only from streaming requests (Issue 4)
- Percentiles exclude error rows (Issue 5)
- Cache token totals included (Issue 7)
- All SUMs wrapped in COALESCE (Issue 11)

**Response:**
```json
{
  "total_requests": 1234,
  "total_errors": 12,
  "total_cost_usd": 4.56,
  "total_input_tokens": 500000,
  "total_output_tokens": 300000,
  "total_cache_creation_tokens": 1200000,
  "total_cache_read_tokens": 3400000,
  "avg_tokens_per_second": 85.3,
  "avg_duration_ms": 2100,
  "avg_ttft_ms": 380,
  "p50_duration_ms": 1800,
  "p95_duration_ms": 5200,
  "p99_duration_ms": 8100,
  "by_model": {
    "claude-sonnet-4-6": {"requests": 900, "cost_usd": 2.10, "avg_tps": 92.0},
    "claude-opus-4-6": {"requests": 334, "cost_usd": 2.46, "avg_tps": 45.0}
  }
}
```

**SQL for main aggregates:**
```sql
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
FROM request_logs
WHERE 1=1 [filters]
```

**SQL for percentiles (separate query, non-error only):**
```sql
SELECT duration_ms FROM request_logs
WHERE is_error = 0 [filters]
ORDER BY duration_ms
```
Then index at len*0.5, len*0.95, len*0.99 in Python.

**SQL for by_model:**
```sql
SELECT model, COUNT(*) as requests,
       COALESCE(SUM(total_cost_usd), 0) as cost_usd,
       AVG(CASE WHEN is_error = 0 AND tokens_per_second > 0 THEN tokens_per_second END) as avg_tps
FROM request_logs
WHERE 1=1 [filters]
GROUP BY model
```

---

#### `GET /v1/stats/timeseries` — Time-bucketed chart data

**Params:** `bucket` (enum: hour/day; default day), `model`, `origin`, `since`, `until`

Dropped `week` bucket (Issue 8 — SQLite %W inconsistent with ISO weeks).

**Response:**
```json
{
  "bucket": "day",
  "data": [
    {
      "period": "2026-04-01",
      "requests": 45,
      "errors": 1,
      "cost_usd": 0.15,
      "input_tokens": 12000,
      "output_tokens": 8000,
      "avg_tokens_per_second": 88.0,
      "avg_duration_ms": 2000
    }
  ]
}
```

**SQL:**
```sql
SELECT
  strftime(?, timestamp) as period,
  COUNT(*) as requests,
  COALESCE(SUM(CASE WHEN is_error = 1 THEN 1 ELSE 0 END), 0) as errors,
  COALESCE(SUM(total_cost_usd), 0) as cost_usd,
  COALESCE(SUM(input_tokens), 0) as input_tokens,
  COALESCE(SUM(output_tokens), 0) as output_tokens,
  AVG(CASE WHEN is_error = 0 AND tokens_per_second > 0 THEN tokens_per_second END) as avg_tps,
  AVG(CASE WHEN is_error = 0 THEN duration_ms END) as avg_duration_ms
FROM request_logs
WHERE 1=1 [filters]
GROUP BY period
ORDER BY period
```

Bucket format mapping:
- `hour` → `'%Y-%m-%dT%H:00:00'`
- `day` → `'%Y-%m-%d'`

---

## Files to Modify

### `app/main.py`
Register analytics router: `app.include_router(analytics_router)`

## Verification
1. `python -m pytest tests/ -v` — all pass
2. `curl https://claude.lawexa.com/v1/logs` — paginated history
3. `curl "https://claude.lawexa.com/v1/logs?model=sonnet"` — LIKE filter works
4. `curl https://claude.lawexa.com/v1/stats` — aggregates with correct avg_tps (no error rows)
5. `curl "https://claude.lawexa.com/v1/stats/timeseries?bucket=hour"` — hourly breakdown
6. Test empty DB — should return zeros, not crash
7. Test with `?is_stream=true` filter
