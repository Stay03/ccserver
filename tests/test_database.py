import pytest

from app.database import close_db, get_db, init_db, insert_request_log
from app.models.metrics import RequestMetrics


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    """Initialize an in-memory database for testing."""
    await init_db(db_path=":memory:")
    yield get_db()
    await close_db()


def _make_metrics(**overrides) -> RequestMetrics:
    defaults = {
        "request_id": "msg_test-session-id",
        "timestamp": "2026-04-07T12:00:00+00:00",
        "model": "claude-sonnet-4-6",
        "input_tokens": 3,
        "output_tokens": 12,
        "cache_creation_input_tokens": 4552,
        "cache_read_input_tokens": 11027,
        "total_cost_usd": 0.020567,
        "duration_ms": 1517,
        "duration_api_ms": 1421,
        "ttft_ms": None,
        "tokens_per_second": 8.44,
        "stop_reason": "end_turn",
        "is_error": False,
        "is_stream": False,
        "num_turns": 1,
        "origin": "proxy",
        "session_id": "test-session-id",
    }
    defaults.update(overrides)
    return RequestMetrics(**defaults)


@pytest.mark.anyio
async def test_insert_and_query(db):
    metrics = _make_metrics()
    await insert_request_log(metrics)

    cursor = await db.execute("SELECT * FROM request_logs WHERE request_id = ?", ("msg_test-session-id",))
    row = await cursor.fetchone()

    assert row is not None
    # Column order matches CREATE TABLE: id, request_id, timestamp, model, ...
    assert row[1] == "msg_test-session-id"
    assert row[2] == "2026-04-07T12:00:00+00:00"
    assert row[3] == "claude-sonnet-4-6"
    assert row[4] == 3   # input_tokens
    assert row[5] == 12  # output_tokens
    assert row[6] == 4552  # cache_creation
    assert row[7] == 11027  # cache_read
    assert abs(row[8] - 0.020567) < 0.0001  # total_cost_usd
    assert row[9] == 1517   # duration_ms
    assert row[10] == 1421  # duration_api_ms
    assert row[11] is None  # ttft_ms (non-streaming)
    assert row[14] == 0     # is_error (False → 0)
    assert row[15] == 0     # is_stream (False → 0)


@pytest.mark.anyio
async def test_insert_streaming_with_ttft(db):
    metrics = _make_metrics(
        request_id="msg_stream-test",
        is_stream=True,
        ttft_ms=450,
    )
    await insert_request_log(metrics)

    cursor = await db.execute("SELECT ttft_ms, is_stream FROM request_logs WHERE request_id = ?", ("msg_stream-test",))
    row = await cursor.fetchone()
    assert row[0] == 450  # ttft_ms
    assert row[1] == 1    # is_stream (True → 1)


@pytest.mark.anyio
async def test_insert_error_request(db):
    metrics = _make_metrics(
        request_id="msg_error-test",
        is_error=True,
        total_cost_usd=0.0,
        tokens_per_second=0.0,
    )
    await insert_request_log(metrics)

    cursor = await db.execute("SELECT is_error, total_cost_usd FROM request_logs WHERE request_id = ?", ("msg_error-test",))
    row = await cursor.fetchone()
    assert row[0] == 1    # is_error (True → 1)
    assert row[1] == 0.0  # total_cost_usd


@pytest.mark.anyio
async def test_duplicate_request_id_ignored(db):
    metrics = _make_metrics()
    await insert_request_log(metrics)
    await insert_request_log(metrics)  # duplicate — should not raise

    cursor = await db.execute("SELECT COUNT(*) FROM request_logs WHERE request_id = ?", ("msg_test-session-id",))
    row = await cursor.fetchone()
    assert row[0] == 1  # only one row


@pytest.mark.anyio
async def test_multiple_inserts(db):
    for i in range(5):
        metrics = _make_metrics(
            request_id=f"msg_test-{i}",
            session_id=f"test-{i}",
        )
        await insert_request_log(metrics)

    cursor = await db.execute("SELECT COUNT(*) FROM request_logs")
    row = await cursor.fetchone()
    assert row[0] == 5


@pytest.mark.anyio
async def test_benchmark_origin(db):
    metrics = _make_metrics(
        request_id="msg_bench-test",
        origin="benchmark",
    )
    await insert_request_log(metrics)

    cursor = await db.execute("SELECT origin FROM request_logs WHERE request_id = ?", ("msg_bench-test",))
    row = await cursor.fetchone()
    assert row[0] == "benchmark"


@pytest.mark.anyio
async def test_insert_without_init():
    """Insert should silently skip when DB is not initialized."""
    from app import database
    original_db = database._db
    database._db = None
    try:
        metrics = _make_metrics()
        await insert_request_log(metrics)  # should not raise
    finally:
        database._db = original_db
