import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import close_db, init_db
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


def _mock_metrics(**overrides) -> RequestMetrics:
    defaults = {
        "request_id": "msg_bench-001",
        "timestamp": "2026-04-07T12:00:00+00:00",
        "model": "claude-sonnet-4-6",
        "input_tokens": 3,
        "output_tokens": 100,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "total_cost_usd": 0.01,
        "duration_ms": 2000,
        "duration_api_ms": 1800,
        "ttft_ms": None,
        "tokens_per_second": 55.5,
        "stop_reason": "end_turn",
        "is_error": False,
        "is_stream": False,
        "num_turns": 1,
        "origin": "benchmark",
        "session_id": "bench-001",
    }
    defaults.update(overrides)
    return RequestMetrics(**defaults)


@pytest.mark.anyio
async def test_benchmark_basic(client):
    call_count = 0

    async def mock_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _mock_metrics(request_id=f"msg_bench-{call_count}")

    with patch("app.routes.benchmark.run_benchmark_request", side_effect=mock_run):
        resp = await client.post("/v1/benchmark", json={
            "prompt": "say hello",
            "num_requests": 3,
            "concurrency": 2,
        })
        assert resp.status_code == 200
        data = resp.json()

        assert data["summary"]["total_requests"] == 3
        assert data["summary"]["successful"] == 3
        assert data["summary"]["failed"] == 0
        assert data["summary"]["total_cost_usd"] > 0
        assert data["summary"]["wall_time_ms"] >= 0
        assert data["summary"]["avg_tokens_per_second"] > 0
        assert len(data["requests"]) == 3
        assert "warning" in data
        assert "$" in data["warning"]


@pytest.mark.anyio
async def test_benchmark_with_failures(client):
    call_count = 0

    async def mock_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            return _mock_metrics(
                request_id=f"msg_bench-{call_count}",
                is_error=True,
                tokens_per_second=0.0,
                total_cost_usd=0.0,
            )
        return _mock_metrics(request_id=f"msg_bench-{call_count}")

    with patch("app.routes.benchmark.run_benchmark_request", side_effect=mock_run):
        resp = await client.post("/v1/benchmark", json={
            "prompt": "say hello",
            "num_requests": 3,
        })
        data = resp.json()

        assert data["summary"]["successful"] == 2
        assert data["summary"]["failed"] == 1
        # avg_tps should exclude the failed request
        assert data["summary"]["avg_tokens_per_second"] == 55.5


@pytest.mark.anyio
async def test_benchmark_all_failures(client):
    async def mock_run(*args, **kwargs):
        return _mock_metrics(is_error=True, tokens_per_second=0.0, duration_ms=0, total_cost_usd=0.0)

    with patch("app.routes.benchmark.run_benchmark_request", side_effect=mock_run):
        resp = await client.post("/v1/benchmark", json={
            "prompt": "say hello",
            "num_requests": 2,
        })
        data = resp.json()

        assert data["summary"]["successful"] == 0
        assert data["summary"]["failed"] == 2
        assert data["summary"]["avg_tokens_per_second"] == 0.0
        assert data["summary"]["p50_duration_ms"] is None
        assert data["summary"]["min_tps"] == 0.0
        assert data["summary"]["max_tps"] == 0.0


@pytest.mark.anyio
async def test_benchmark_streaming(client):
    async def mock_run(*args, **kwargs):
        return _mock_metrics(is_stream=True, ttft_ms=450)

    with patch("app.routes.benchmark.run_benchmark_request", side_effect=mock_run):
        resp = await client.post("/v1/benchmark", json={
            "prompt": "say hello",
            "num_requests": 2,
            "stream": True,
        })
        data = resp.json()

        assert data["summary"]["avg_ttft_ms"] == 450
        assert data["requests"][0]["ttft_ms"] == 450


@pytest.mark.anyio
async def test_benchmark_validation_zero_requests(client):
    resp = await client.post("/v1/benchmark", json={
        "prompt": "hello",
        "num_requests": 0,
    })
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_benchmark_validation_zero_concurrency(client):
    resp = await client.post("/v1/benchmark", json={
        "prompt": "hello",
        "concurrency": 0,
    })
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_benchmark_concurrency_capped(client):
    calls = []

    async def mock_run(*args, **kwargs):
        calls.append(1)
        return _mock_metrics(request_id=f"msg_bench-{len(calls)}")

    with patch("app.routes.benchmark.run_benchmark_request", side_effect=mock_run):
        with patch("app.routes.benchmark.settings") as mock_settings:
            mock_settings.benchmark_max_concurrency = 2
            mock_settings.benchmark_max_requests = 5
            mock_settings.default_model = "sonnet"

            resp = await client.post("/v1/benchmark", json={
                "prompt": "hello",
                "concurrency": 100,
                "num_requests": 3,
            })
            data = resp.json()
            # Should still complete 3 requests (capped concurrency, not num_requests)
            assert data["summary"]["total_requests"] == 3


@pytest.mark.anyio
async def test_benchmark_exception_handling(client):
    call_count = 0

    async def mock_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("CLI exploded")
        return _mock_metrics(request_id=f"msg_bench-{call_count}")

    with patch("app.routes.benchmark.run_benchmark_request", side_effect=mock_run):
        resp = await client.post("/v1/benchmark", json={
            "prompt": "hello",
            "num_requests": 2,
        })
        data = resp.json()

        assert data["summary"]["total_requests"] == 2
        assert data["summary"]["failed"] == 1
        assert data["summary"]["successful"] == 1
        failed = [r for r in data["requests"] if r["is_error"]]
        assert len(failed) == 1
        assert "CLI exploded" in failed[0]["error_message"]


@pytest.mark.anyio
async def test_benchmark_percentiles(client):
    durations = [1000, 2000, 3000, 4000, 5000]
    call_count = 0

    async def mock_run(*args, **kwargs):
        nonlocal call_count
        d = durations[call_count]
        call_count += 1
        return _mock_metrics(request_id=f"msg_bench-{call_count}", duration_ms=d)

    with patch("app.routes.benchmark.run_benchmark_request", side_effect=mock_run):
        resp = await client.post("/v1/benchmark", json={
            "prompt": "hello",
            "num_requests": 5,
        })
        data = resp.json()

        assert data["summary"]["p50_duration_ms"] is not None
        assert data["summary"]["p95_duration_ms"] is not None
        assert data["summary"]["p50_duration_ms"] <= data["summary"]["p95_duration_ms"]
