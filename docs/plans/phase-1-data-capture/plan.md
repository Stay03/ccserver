# Phase 1: Data Capture Layer

## Goal
Capture all metrics the CLI already provides but the code currently discards. Store in SQLite. No new API endpoints yet — just the foundation.

## What's currently discarded
The CLI result JSON contains these fields that `parse_cli_result()` throws away:
- `total_cost_usd`
- `duration_ms`
- `duration_api_ms`
- `usage.cache_creation_input_tokens`
- `usage.cache_read_input_tokens`
- `num_turns`

## Database Schema

SQLite with WAL mode via `aiosqlite`. Single table:

```sql
CREATE TABLE request_logs (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id                  TEXT NOT NULL UNIQUE,
    timestamp                   TEXT NOT NULL,          -- ISO 8601 UTC
    model                       TEXT NOT NULL,
    input_tokens                INTEGER DEFAULT 0,
    output_tokens               INTEGER DEFAULT 0,
    cache_creation_input_tokens INTEGER DEFAULT 0,
    cache_read_input_tokens     INTEGER DEFAULT 0,
    total_cost_usd              REAL DEFAULT 0.0,
    duration_ms                 INTEGER DEFAULT 0,
    duration_api_ms             INTEGER DEFAULT 0,
    ttft_ms                     INTEGER,                -- NULL for non-streaming
    tokens_per_second           REAL DEFAULT 0.0,
    stop_reason                 TEXT,
    is_error                    INTEGER DEFAULT 0,
    is_stream                   INTEGER DEFAULT 0,
    num_turns                   INTEGER DEFAULT 1,
    origin                      TEXT DEFAULT 'proxy',   -- 'proxy' or 'benchmark'
    session_id                  TEXT
);

CREATE INDEX idx_timestamp ON request_logs(timestamp);
CREATE INDEX idx_model ON request_logs(model);
CREATE INDEX idx_origin ON request_logs(origin);
```

## Files to Create

### `app/models/metrics.py`
Python dataclass (not Pydantic — internal only):
```python
@dataclasses.dataclass
class RequestMetrics:
    request_id: str
    timestamp: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_cost_usd: float = 0.0
    duration_ms: int = 0
    duration_api_ms: int = 0
    ttft_ms: int | None = None
    tokens_per_second: float = 0.0
    stop_reason: str | None = None
    is_error: bool = False
    is_stream: bool = False
    num_turns: int = 1
    origin: str = "proxy"
    session_id: str = ""
```

### `app/database.py`
- `init_db()` — open aiosqlite connection, enable WAL, create table
- `close_db()` — close connection
- `insert_request_log(metrics: RequestMetrics)` — async INSERT
- `get_db()` — return shared connection for queries

## Files to Modify

### `requirements.txt`
Add: `aiosqlite>=0.20.0`

### `app/config.py`
Add setting: `db_path: str = "claude_proxy.db"`

### `app/models/response.py`
Add to `Usage` model:
```python
cache_creation_input_tokens: int = 0
cache_read_input_tokens: int = 0
```

### `app/services/converter.py`
Change `parse_cli_result()` to return `(MessagesResponse, RequestMetrics)`:
- Extract `total_cost_usd`, `duration_ms`, `duration_api_ms`, `num_turns`, cache tokens from `result_event`
- Compute `tokens_per_second = output_tokens / (duration_api_ms / 1000)` when `duration_api_ms > 0`
- Populate Usage with cache token fields

### `app/services/claude_cli.py`
**Depends on Phase 0 (streaming fix) being complete.**

**`run_claude()`:**
- Unpack `(response, metrics)` from `parse_cli_result()`
- Set `metrics.is_stream = False`
- `asyncio.create_task(database.insert_request_log(metrics))`
- Return `response` (unchanged for callers)

**`stream_claude()`:**
After Phase 0, the streaming loop handles `stream_event` and `result` events. Add:
- Record `start_time = time.monotonic()` before loop
- On first `stream_event` with inner type `content_block_delta`: compute `ttft_ms = (time.monotonic() - start_time) * 1000`
- On `result` event: build `RequestMetrics` from event data (same fields as `parse_cli_result`), set `is_stream=True`, `ttft_ms`
- After handling `result`: `asyncio.create_task(database.insert_request_log(metrics))`

### `app/main.py`
Add lifespan context manager:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    yield
    await database.close_db()

app = FastAPI(..., lifespan=lifespan)
```

### `tests/test_converter.py`
Update 3 `parse_cli_result` tests to unpack tuple: `response, metrics = parse_cli_result(...)`

## Implementation Order
1. `app/models/metrics.py` — no dependencies
2. `app/database.py` — depends on metrics + config
3. `app/config.py` — add db_path
4. `requirements.txt` — add aiosqlite
5. `app/models/response.py` — add cache fields to Usage
6. `app/services/converter.py` — return tuple with metrics
7. `tests/test_converter.py` — fix tests for new return type
8. `app/services/claude_cli.py` — wire in metrics capture + TTFT
9. `app/main.py` — add lifespan

## Verification
1. Run tests: `python -m pytest tests/ -v` — all should pass
2. Start server, make a request
3. Check DB: `sqlite3 claude_proxy.db "SELECT * FROM request_logs;"`
4. Verify: cost, duration, tokens_per_second are populated
