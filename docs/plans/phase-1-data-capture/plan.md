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
- `modelUsage` (resolved model name + per-model cost breakdown)

## Database Schema

SQLite with WAL mode via `aiosqlite`. Single table:

```sql
CREATE TABLE request_logs (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id                  TEXT NOT NULL UNIQUE,
    timestamp                   TEXT NOT NULL,          -- ISO 8601 UTC
    model                       TEXT NOT NULL,          -- RESOLVED model name (e.g. "claude-sonnet-4-6", not "sonnet")
    input_tokens                INTEGER DEFAULT 0,
    output_tokens               INTEGER DEFAULT 0,
    cache_creation_input_tokens INTEGER DEFAULT 0,
    cache_read_input_tokens     INTEGER DEFAULT 0,
    total_cost_usd              REAL DEFAULT 0.0,
    duration_ms                 INTEGER DEFAULT 0,
    duration_api_ms             INTEGER DEFAULT 0,
    ttft_ms                     INTEGER,                -- NULL for non-streaming
    tokens_per_second           REAL DEFAULT 0.0,       -- 0.0 if output_tokens=0 or duration_api_ms=0
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

### Issue 6 fix: `model` stores the RESOLVED name
Phase 0 adds `resolve_model()` which extracts the real model from `modelUsage` keys.
Both non-streaming and streaming paths use this, so stats grouped by model will be consistent
regardless of whether the client sends `"sonnet"` or `"claude-sonnet-4-6"`.

### Issue 7 fix: `request_id` uses `session_id` consistently
Both non-streaming and streaming paths generate `request_id` from the `result` event's `session_id`.
The streaming path's `message_start` has a different API message ID — that's fine for SSE forwarding,
but for metrics logging we use `session_id` since it's always present in both paths.
Format: `msg_{session_id}` (full UUID, not truncated).

---

## Files to Create

### `app/models/metrics.py`
Python dataclass (not Pydantic — internal only):
```python
import dataclasses
from datetime import datetime, timezone

@dataclasses.dataclass
class RequestMetrics:
    request_id: str
    timestamp: str                          # ISO 8601 UTC
    model: str                              # RESOLVED model name from modelUsage
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_cost_usd: float = 0.0
    duration_ms: int = 0
    duration_api_ms: int = 0
    ttft_ms: int | None = None              # NULL for non-streaming
    tokens_per_second: float = 0.0
    stop_reason: str | None = None
    is_error: bool = False
    is_stream: bool = False
    num_turns: int = 1
    origin: str = "proxy"
    session_id: str = ""

def compute_tps(output_tokens: int, duration_api_ms: int) -> float:
    """Compute tokens per second, safe from division by zero."""
    if output_tokens <= 0 or duration_api_ms <= 0:
        return 0.0
    return output_tokens / (duration_api_ms / 1000)

def build_metrics_from_result(result_event: dict, is_stream: bool, ttft_ms: int | None = None, origin: str = "proxy") -> RequestMetrics:
    """Build RequestMetrics from a CLI result event. Used by both streaming and non-streaming paths."""
    session_id = result_event.get("session_id", "")
    usage = result_event.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    duration_api_ms = result_event.get("duration_api_ms", 0)

    # Resolve model from modelUsage keys (Phase 0 adds resolve_model to converter)
    model_usage = result_event.get("modelUsage", {})
    model = next(iter(model_usage), "unknown") if model_usage else "unknown"

    return RequestMetrics(
        request_id=f"msg_{session_id}" if session_id else f"msg_{uuid.uuid4().hex}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
        cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        total_cost_usd=result_event.get("total_cost_usd", 0.0),
        duration_ms=result_event.get("duration_ms", 0),
        duration_api_ms=duration_api_ms,
        ttft_ms=ttft_ms,
        tokens_per_second=compute_tps(output_tokens, duration_api_ms),
        stop_reason=result_event.get("stop_reason"),
        is_error=result_event.get("is_error", False),
        is_stream=is_stream,
        num_turns=result_event.get("num_turns", 1),
        origin=origin,
        session_id=session_id,
    )
```

Key fixes:
- **Issue 12 fixed**: `compute_tps()` checks both `output_tokens > 0` AND `duration_api_ms > 0`
- **Issue 6 fixed**: Model resolved from `modelUsage` keys
- **Issue 7 fixed**: `request_id` uses full `session_id` (not truncated)
- Shared `build_metrics_from_result()` used by both streaming and non-streaming — no duplication

### `app/database.py`
```python
import logging
import aiosqlite
from app.config import settings
from app.models.metrics import RequestMetrics
from pathlib import Path

logger = logging.getLogger(__name__)

_db: aiosqlite.Connection | None = None

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS request_logs (
    ...schema from above...
);
"""

async def init_db():
    global _db
    db_path = Path(settings.db_path).resolve()    # Issue 10 fix: resolve to absolute
    _db = await aiosqlite.connect(str(db_path))
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.executescript(CREATE_TABLE + indexes)
    await _db.commit()

async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None

async def insert_request_log(metrics: RequestMetrics):
    """Insert a request log row. Errors are logged, never raised — analytics should not break requests."""
    if not _db:
        logger.warning("Database not initialized, skipping metrics insert")
        return
    try:                                         # Issue 11 fix: try/except with logging
        await _db.execute(
            "INSERT OR IGNORE INTO request_logs (...) VALUES (...)",
            (metrics fields...),
        )
        await _db.commit()
    except Exception:
        logger.exception("Failed to insert request log")

def get_db() -> aiosqlite.Connection | None:
    return _db
```

Key fixes:
- **Issue 10 fixed**: `Path(settings.db_path).resolve()` — always absolute path
- **Issue 11 fixed**: `try/except` with `logger.exception()` in `insert_request_log`
- `INSERT OR IGNORE` prevents crash on duplicate request_id

---

## Files to Modify

### `requirements.txt`
Add: `aiosqlite>=0.20.0`

### `app/config.py`
Add setting:
```python
db_path: str = "claude_proxy.db"    # Resolved to absolute path in database.py
```

### `app/models/response.py`
Add to `Usage` model:
```python
cache_creation_input_tokens: int = 0
cache_read_input_tokens: int = 0
```

### `app/services/converter.py`
Change `parse_cli_result()` to return `(MessagesResponse, RequestMetrics)`:
- Use `resolve_model()` (from Phase 0) for the response model
- Call `build_metrics_from_result()` for the metrics
- Populate Usage with cache token fields

```python
def parse_cli_result(result_event: dict, model: str) -> tuple[MessagesResponse, RequestMetrics]:
    resolved = resolve_model(result_event, model)
    session_id = result_event.get("session_id", "")
    msg_id = f"msg_{session_id}" if session_id else f"msg_{uuid.uuid4().hex}"
    result_text = result_event.get("result", "")
    usage_data = result_event.get("usage", {})
    stop_reason = map_stop_reason(result_event.get("stop_reason"))

    response = MessagesResponse(
        id=msg_id,
        model=resolved,
        content=[ContentBlock(type="text", text=result_text)],
        stop_reason=stop_reason,
        usage=Usage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            cache_creation_input_tokens=usage_data.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=usage_data.get("cache_read_input_tokens", 0),
        ),
    )

    metrics = build_metrics_from_result(result_event, is_stream=False)

    return response, metrics
```

### `app/services/claude_cli.py`
**Depends on Phase 0 (streaming fix) being complete.**

**`run_claude()`:**
- Unpack `(response, metrics)` from `parse_cli_result()`
- `await database.insert_request_log(metrics)` — direct await, not create_task
  (non-streaming response is already complete, no client waiting for more data)

**`stream_claude()`:**
After Phase 0, the streaming loop handles `stream_event` and `result` events. Add:
- Record `start_time = time.monotonic()` before the event loop
- Track `first_delta_seen = False`
- On first `stream_event` with inner type `content_block_delta`:
  - Compute `ttft_ms = int((time.monotonic() - start_time) * 1000)`
  - Set `first_delta_seen = True`
  - Note: this TTFT includes CLI startup time (documented, not a bug)
- On `result` event:
  - Call `build_metrics_from_result(result_event, is_stream=True, ttft_ms=ttft_ms)`
  - `await database.insert_request_log(metrics)` — **Issue 8 fix**: direct await after last yield,
    not `create_task` which may not execute after generator completes

### `app/main.py`
Add lifespan context manager:
```python
from contextlib import asynccontextmanager
from app import database

@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    yield
    await database.close_db()

app = FastAPI(..., lifespan=lifespan)
```

### `tests/test_converter.py`
- Update 3 `parse_cli_result` tests to unpack tuple: `response, metrics = parse_cli_result(...)`
- **Issue 13 fix**: Update test data to use `"stop_reason": "end_turn"` (matching real CLI output)
- Add `modelUsage` to test data so resolved model is tested
- Add assertions on `metrics` fields: `total_cost_usd`, `duration_ms`, `tokens_per_second`
- Add test for `compute_tps` edge cases: zero tokens, zero duration

---

## Implementation Order
1. `app/models/metrics.py` — no dependencies
2. `app/database.py` — depends on metrics + config
3. `app/config.py` — add db_path
4. `requirements.txt` — add aiosqlite
5. `app/models/response.py` — add cache fields to Usage
6. `app/services/converter.py` — return tuple with metrics, use resolve_model
7. `tests/test_converter.py` — fix tests for new return type + realistic data
8. `app/services/claude_cli.py` — wire in metrics capture + TTFT
9. `app/main.py` — add lifespan
10. Run all tests

## Verification
1. Run tests: `python -m pytest tests/ -v` — all should pass
2. Start server, make requests (both streaming and non-streaming)
3. Check DB: `sqlite3 claude_proxy.db "SELECT request_id, model, total_cost_usd, duration_ms, tokens_per_second, ttft_ms, is_stream FROM request_logs;"`
4. Verify:
   - `model` shows `claude-sonnet-4-6` (not `sonnet`)
   - `total_cost_usd` > 0
   - `tokens_per_second` > 0
   - `ttft_ms` is populated for streaming, NULL for non-streaming
   - `is_stream` is 1 for streaming, 0 for non-streaming
5. Check no new files in `~/.claude/sessions/` (--no-session-persistence working)
