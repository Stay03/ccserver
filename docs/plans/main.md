# CCServer Analytics, Logging & Benchmarking

## Overview
Add OpenRouter-style analytics to the Claude Code proxy: per-request logging, aggregate stats, timeseries charts, and active benchmarking. Also fix streaming to use the CLI's native Anthropic-format events.

## Phases

### [Phase 0: Fix Streaming](phase-0-fix-streaming/plan.md)
**Pre-requisite fix** — the CLI emits proper Anthropic SSE events via `stream_event` wrappers (requires both `--verbose` AND `--include-partial-messages`). Current code ignores these and reconstructs deltas from `assistant` events (hacky). Refactor to forward `stream_event.event` directly.

**Modified:** `app/services/claude_cli.py`, `tests/test_api.py`

---

### [Phase 1: Data Capture Layer](phase-1-data-capture/plan.md)
**Foundation** — everything depends on this. Capture all metrics from CLI responses (cost, timing, cache tokens, TPS) and persist to SQLite. Fire-and-forget logging so requests aren't slowed.

**New files:** `app/database.py`, `app/models/metrics.py`
**Modified:** converter, CLI runner, main, config, response models, tests

---

### [Phase 2: Query Endpoints](phase-2-query-endpoints/plan.md)
**Read the data** — three GET endpoints exposing logged data.

- `GET /v1/logs` — paginated request history (OpenRouter Generations view)
- `GET /v1/stats` — aggregate metrics (spend, tokens, TPS, latency percentiles)
- `GET /v1/stats/timeseries` — time-bucketed data for charts

**New files:** `app/routes/analytics.py`

---

### [Phase 3: Benchmark](phase-3-benchmark/plan.md)
**Active testing** — fire N concurrent real requests, measure TPS/latency/cost.

- `POST /v1/benchmark` — controlled performance test with summary report
- Costs real tokens, results logged with `origin=benchmark`

**New files:** `app/routes/benchmark.py`, `app/models/benchmark.py`

---

## Dependency Order
```
Phase 0 (fix streaming — do first)
    └── Phase 1 (data capture — depends on clean streaming for TTFT)
            ├── Phase 2 (query endpoints — independent)
            └── Phase 3 (benchmark — independent)
```

## Data Flow
```
Request → POST /v1/messages → CLI subprocess → parse result
                                                    ↓
                                    ┌───────────────┴───────────────┐
                                    ↓                               ↓
                             Return response              Background: SQLite INSERT
                             to client (fast)             (fire-and-forget)
                                                                    ↓
                                                    GET /v1/logs, /v1/stats ← read
```

## CLI Event Types (confirmed from live output)

### Non-streaming (`--output-format json`)
Single JSON line: `{"type":"result", "total_cost_usd":..., "duration_ms":..., "usage":{...}, ...}`

### Streaming (`--output-format stream-json --verbose`)
| Order | CLI `type` | Inner `event.type` | Action |
|-------|-----------|-------------------|--------|
| 1 | `system` | — | Extract session metadata, skip SSE |
| 2 | `stream_event` | `message_start` | **Forward as SSE** |
| 3 | `stream_event` | `content_block_start` | **Forward as SSE** |
| 4-N | `stream_event` | `content_block_delta` | **Forward as SSE** (text chunks) |
| N+1 | `assistant` | — | **Skip** (redundant snapshot) |
| N+2 | `stream_event` | `content_block_stop` | **Forward as SSE** |
| N+3 | `stream_event` | `message_delta` | **Forward as SSE** (usage + stop_reason) |
| N+4 | `stream_event` | `message_stop` | **Forward as SSE** |
| N+5 | `rate_limit_event` | — | Log/skip |
| N+6 | `result` | — | Capture metrics (cost, duration, tokens) |
