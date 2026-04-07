import pytest
from httpx import ASGITransport, AsyncClient

from app.database import close_db, init_db, insert_request_log
from app.main import app
from app.models.metrics import RequestMetrics


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    await init_db(db_path=":memory:")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await close_db()


def _make_metrics(**overrides) -> RequestMetrics:
    defaults = {
        "request_id": "msg_test-001",
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
        "session_id": "test-001",
    }
    defaults.update(overrides)
    return RequestMetrics(**defaults)


async def _seed_data():
    """Insert diverse test data."""
    await insert_request_log(_make_metrics(
        request_id="msg_1", session_id="s1",
        timestamp="2026-04-07T10:00:00+00:00",
        model="claude-sonnet-4-6",
        output_tokens=100, duration_ms=2000, duration_api_ms=1800,
        tokens_per_second=55.5, total_cost_usd=0.01,
        is_stream=False,
    ))
    await insert_request_log(_make_metrics(
        request_id="msg_2", session_id="s2",
        timestamp="2026-04-07T10:30:00+00:00",
        model="claude-sonnet-4-6",
        output_tokens=200, duration_ms=3000, duration_api_ms=2800,
        tokens_per_second=71.4, total_cost_usd=0.02,
        is_stream=True, ttft_ms=450,
    ))
    await insert_request_log(_make_metrics(
        request_id="msg_3", session_id="s3",
        timestamp="2026-04-07T11:00:00+00:00",
        model="claude-opus-4-6",
        output_tokens=50, duration_ms=5000, duration_api_ms=4800,
        tokens_per_second=10.4, total_cost_usd=0.05,
        is_stream=False,
    ))
    await insert_request_log(_make_metrics(
        request_id="msg_4", session_id="s4",
        timestamp="2026-04-07T11:30:00+00:00",
        model="claude-sonnet-4-6",
        output_tokens=0, duration_ms=500, duration_api_ms=0,
        tokens_per_second=0.0, total_cost_usd=0.0,
        is_error=True, is_stream=False,
    ))
    await insert_request_log(_make_metrics(
        request_id="msg_5", session_id="s5",
        timestamp="2026-04-08T09:00:00+00:00",
        model="claude-sonnet-4-6",
        output_tokens=300, duration_ms=4000, duration_api_ms=3800,
        tokens_per_second=78.9, total_cost_usd=0.03,
        is_stream=True, ttft_ms=600,
        origin="benchmark",
    ))


# =========== /v1/logs ===========

@pytest.mark.anyio
async def test_logs_returns_all(client):
    await _seed_data()
    resp = await client.get("/v1/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 5
    assert data["pagination"]["total"] == 5
    assert data["pagination"]["page"] == 1


@pytest.mark.anyio
async def test_logs_pagination(client):
    await _seed_data()
    resp = await client.get("/v1/logs", params={"per_page": 2, "page": 1})
    data = resp.json()
    assert len(data["data"]) == 2
    assert data["pagination"]["total"] == 5
    assert data["pagination"]["total_pages"] == 3

    resp2 = await client.get("/v1/logs", params={"per_page": 2, "page": 3})
    data2 = resp2.json()
    assert len(data2["data"]) == 1


@pytest.mark.anyio
async def test_logs_ordered_desc(client):
    await _seed_data()
    resp = await client.get("/v1/logs")
    data = resp.json()["data"]
    timestamps = [d["timestamp"] for d in data]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.anyio
async def test_logs_filter_model_like(client):
    await _seed_data()
    resp = await client.get("/v1/logs", params={"model": "sonnet"})
    data = resp.json()
    assert all("sonnet" in d["model"] for d in data["data"])
    assert data["pagination"]["total"] == 4


@pytest.mark.anyio
async def test_logs_filter_model_exact(client):
    await _seed_data()
    resp = await client.get("/v1/logs", params={"model": "claude-opus-4-6"})
    data = resp.json()
    assert data["pagination"]["total"] == 1
    assert data["data"][0]["model"] == "claude-opus-4-6"


@pytest.mark.anyio
async def test_logs_filter_origin(client):
    await _seed_data()
    resp = await client.get("/v1/logs", params={"origin": "benchmark"})
    data = resp.json()
    assert data["pagination"]["total"] == 1
    assert data["data"][0]["origin"] == "benchmark"


@pytest.mark.anyio
async def test_logs_filter_is_stream(client):
    await _seed_data()
    resp = await client.get("/v1/logs", params={"is_stream": True})
    data = resp.json()
    assert all(d["is_stream"] is True for d in data["data"])
    assert data["pagination"]["total"] == 2


@pytest.mark.anyio
async def test_logs_filter_since_until(client):
    await _seed_data()
    resp = await client.get("/v1/logs", params={
        "since": "2026-04-07T11:00:00",
        "until": "2026-04-07T12:00:00",
    })
    data = resp.json()
    assert data["pagination"]["total"] == 2  # msg_3 and msg_4


@pytest.mark.anyio
async def test_logs_empty_db(client):
    resp = await client.get("/v1/logs")
    data = resp.json()
    assert data["data"] == []
    assert data["pagination"]["total"] == 0
    assert data["pagination"]["total_pages"] == 0


@pytest.mark.anyio
async def test_logs_bool_fields(client):
    await _seed_data()
    resp = await client.get("/v1/logs")
    data = resp.json()["data"]
    for entry in data:
        assert isinstance(entry["is_error"], bool)
        assert isinstance(entry["is_stream"], bool)


# =========== /v1/stats ===========

@pytest.mark.anyio
async def test_stats_aggregates(client):
    await _seed_data()
    resp = await client.get("/v1/stats")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_requests"] == 5
    assert data["total_errors"] == 1
    assert data["total_cost_usd"] > 0
    assert data["total_input_tokens"] >= 0
    assert data["total_output_tokens"] > 0
    assert data["total_cache_creation_tokens"] >= 0
    assert data["total_cache_read_tokens"] >= 0


@pytest.mark.anyio
async def test_stats_avg_tps_excludes_errors(client):
    await _seed_data()
    resp = await client.get("/v1/stats")
    data = resp.json()
    # Error row has tps=0, should be excluded from average
    assert data["avg_tokens_per_second"] > 0


@pytest.mark.anyio
async def test_stats_avg_ttft_streaming_only(client):
    await _seed_data()
    resp = await client.get("/v1/stats")
    data = resp.json()
    # Only msg_2 (ttft=450) and msg_5 (ttft=600) are streaming with ttft
    assert data["avg_ttft_ms"] == round((450 + 600) / 2)


@pytest.mark.anyio
async def test_stats_percentiles(client):
    await _seed_data()
    resp = await client.get("/v1/stats")
    data = resp.json()
    assert data["p50_duration_ms"] is not None
    assert data["p95_duration_ms"] is not None
    assert data["p99_duration_ms"] is not None
    assert data["p50_duration_ms"] <= data["p95_duration_ms"]
    assert data["p95_duration_ms"] <= data["p99_duration_ms"]


@pytest.mark.anyio
async def test_stats_by_model(client):
    await _seed_data()
    resp = await client.get("/v1/stats")
    data = resp.json()
    assert "claude-sonnet-4-6" in data["by_model"]
    assert "claude-opus-4-6" in data["by_model"]
    assert data["by_model"]["claude-sonnet-4-6"]["requests"] == 4
    assert data["by_model"]["claude-opus-4-6"]["requests"] == 1


@pytest.mark.anyio
async def test_stats_filter_model(client):
    await _seed_data()
    resp = await client.get("/v1/stats", params={"model": "opus"})
    data = resp.json()
    assert data["total_requests"] == 1


@pytest.mark.anyio
async def test_stats_empty_db(client):
    resp = await client.get("/v1/stats")
    data = resp.json()
    assert data["total_requests"] == 0
    assert data["total_errors"] == 0
    assert data["total_cost_usd"] == 0
    assert data["avg_tokens_per_second"] == 0.0
    assert data["avg_ttft_ms"] is None
    assert data["p50_duration_ms"] is None
    assert data["by_model"] == {}


# =========== /v1/stats/timeseries ===========

@pytest.mark.anyio
async def test_timeseries_day_bucket(client):
    await _seed_data()
    resp = await client.get("/v1/stats/timeseries", params={"bucket": "day"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["bucket"] == "day"
    assert len(data["data"]) == 2  # 2026-04-07 and 2026-04-08
    periods = [d["period"] for d in data["data"]]
    assert "2026-04-07" in periods
    assert "2026-04-08" in periods


@pytest.mark.anyio
async def test_timeseries_hour_bucket(client):
    await _seed_data()
    resp = await client.get("/v1/stats/timeseries", params={"bucket": "hour"})
    data = resp.json()
    assert data["bucket"] == "hour"
    assert len(data["data"]) >= 3  # 10:00, 11:00 on apr 7 + 09:00 on apr 8


@pytest.mark.anyio
async def test_timeseries_5min_bucket(client):
    await _seed_data()
    resp = await client.get("/v1/stats/timeseries", params={"bucket": "5min"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["bucket"] == "5min"
    # Data spans 10:00, 10:30, 11:00, 11:30 on apr 7 + 09:00 on apr 8
    # At 5min granularity, each timestamp falls in its own 5min bucket
    assert len(data["data"]) >= 4
    # Periods should look like "2026-04-07T10:00" format
    for d in data["data"]:
        assert "T" in d["period"]
        assert len(d["period"]) >= 16  # "2026-04-07T10:00"


@pytest.mark.anyio
async def test_timeseries_invalid_bucket(client):
    resp = await client.get("/v1/stats/timeseries", params={"bucket": "week"})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_timeseries_filter_model(client):
    await _seed_data()
    resp = await client.get("/v1/stats/timeseries", params={"model": "opus"})
    data = resp.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["requests"] == 1


@pytest.mark.anyio
async def test_timeseries_empty_db(client):
    resp = await client.get("/v1/stats/timeseries")
    data = resp.json()
    assert data["data"] == []


@pytest.mark.anyio
async def test_timeseries_avg_tps_excludes_errors(client):
    await _seed_data()
    resp = await client.get("/v1/stats/timeseries", params={"bucket": "day"})
    data = resp.json()
    for bucket in data["data"]:
        # avg_tps should be > 0 since error rows are excluded
        if bucket["requests"] > bucket["errors"]:
            assert bucket["avg_tokens_per_second"] > 0
