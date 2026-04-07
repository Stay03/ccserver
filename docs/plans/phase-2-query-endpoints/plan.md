# Phase 2: Query Endpoints

## Goal
Three GET endpoints to expose logged data — paginated history, aggregate stats, and timeseries for charts.

## Depends on
Phase 1 (data must be in SQLite first)

## Files to Create

### `app/routes/analytics.py`

#### `GET /v1/logs` — Paginated request history

**Params:** `page` (default 1), `per_page` (default 50, max 200), `model`, `origin`, `since` (ISO), `until` (ISO)

**Response:**
```json
{
  "data": [
    {
      "request_id": "msg_xxx",
      "timestamp": "2026-04-07T12:00:00Z",
      "model": "sonnet",
      "input_tokens": 150,
      "output_tokens": 300,
      "total_cost_usd": 0.003,
      "duration_ms": 3200,
      "ttft_ms": 450,
      "tokens_per_second": 93.75,
      "stop_reason": "end_turn",
      "is_error": false,
      "is_stream": true,
      "origin": "proxy"
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

**SQL:** `SELECT ... FROM request_logs WHERE [filters] ORDER BY timestamp DESC LIMIT ? OFFSET ?`
Plus `SELECT COUNT(*) ...` for pagination total.

---

#### `GET /v1/stats` — Aggregate metrics

**Params:** `model`, `origin`, `since`, `until`

**Response:**
```json
{
  "total_requests": 1234,
  "total_errors": 12,
  "total_cost_usd": 4.56,
  "total_input_tokens": 500000,
  "total_output_tokens": 300000,
  "avg_tokens_per_second": 85.3,
  "avg_duration_ms": 2100,
  "avg_ttft_ms": 380,
  "p50_duration_ms": 1800,
  "p95_duration_ms": 5200,
  "p99_duration_ms": 8100,
  "by_model": {
    "sonnet": {"requests": 900, "cost_usd": 2.10, "avg_tps": 92.0},
    "opus": {"requests": 334, "cost_usd": 2.46, "avg_tps": 45.0}
  }
}
```

**SQL:** 
- Aggregate: `SELECT COUNT(*), SUM(total_cost_usd), AVG(tokens_per_second), ... FROM request_logs WHERE [filters]`
- By model: `SELECT model, COUNT(*), SUM(total_cost_usd), AVG(tokens_per_second) FROM request_logs WHERE [filters] GROUP BY model`
- Percentiles: Fetch sorted `duration_ms` values and index at p50/p95/p99 positions

---

#### `GET /v1/stats/timeseries` — Time-bucketed chart data

**Params:** `bucket` (enum: hour/day/week, default day), `model`, `since`, `until`

**Response:**
```json
{
  "bucket": "day",
  "data": [
    {
      "period": "2026-04-01",
      "requests": 45,
      "cost_usd": 0.15,
      "input_tokens": 12000,
      "output_tokens": 8000,
      "avg_tokens_per_second": 88.0,
      "avg_duration_ms": 2000,
      "errors": 1
    }
  ]
}
```

**SQL:** `SELECT strftime(?, timestamp) as period, COUNT(*), SUM(...), AVG(...) FROM request_logs WHERE [filters] GROUP BY period ORDER BY period`

Bucket format mapping:
- `hour` → `'%Y-%m-%dT%H:00:00'`
- `day` → `'%Y-%m-%d'`
- `week` → `'%Y-W%W'`

## Files to Modify

### `app/main.py`
Register analytics router: `app.include_router(analytics_router)`

## Verification
1. Make several requests to `/v1/messages` first (to populate data)
2. `curl https://claude.lawexa.com/v1/logs` — see request history
3. `curl https://claude.lawexa.com/v1/stats` — see aggregates with cost, TPS, percentiles
4. `curl "https://claude.lawexa.com/v1/stats/timeseries?bucket=hour"` — see hourly breakdown
5. Test filters: `curl "https://claude.lawexa.com/v1/logs?model=sonnet&per_page=10"`
