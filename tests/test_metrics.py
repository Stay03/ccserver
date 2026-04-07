from app.models.metrics import (
    RequestMetrics,
    build_metrics_from_result,
    compute_tps,
)


class TestComputeTps:
    def test_normal_case(self):
        # 327 tokens in 9306ms = 35.13 tok/s (confirmed from live test)
        tps = compute_tps(327, 9306)
        assert 35.0 < tps < 35.2

    def test_zero_output_tokens(self):
        assert compute_tps(0, 1000) == 0.0

    def test_zero_duration(self):
        assert compute_tps(100, 0) == 0.0

    def test_both_zero(self):
        assert compute_tps(0, 0) == 0.0

    def test_negative_tokens(self):
        assert compute_tps(-1, 1000) == 0.0

    def test_negative_duration(self):
        assert compute_tps(100, -1) == 0.0

    def test_small_values(self):
        # 12 tokens in 1421ms = 8.44 tok/s
        tps = compute_tps(12, 1421)
        assert 8.4 < tps < 8.5


def _make_result_event(**overrides):
    """Realistic CLI result event from confirmed live output."""
    event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 1517,
        "duration_api_ms": 1421,
        "num_turns": 1,
        "result": "Hello! How can I help you today?",
        "stop_reason": "end_turn",
        "session_id": "50f85a80-7ef8-4ce9-8163-2c64db7e19e4",
        "total_cost_usd": 0.020567,
        "usage": {
            "input_tokens": 3,
            "output_tokens": 12,
            "cache_creation_input_tokens": 4552,
            "cache_read_input_tokens": 11027,
        },
        "modelUsage": {
            "claude-sonnet-4-6": {
                "inputTokens": 3,
                "outputTokens": 12,
                "costUSD": 0.020567,
            }
        },
    }
    event.update(overrides)
    return event


class TestBuildMetricsFromResult:
    def test_successful_non_streaming(self):
        event = _make_result_event()
        metrics = build_metrics_from_result(event, is_stream=False)

        assert metrics.model == "claude-sonnet-4-6"
        assert metrics.input_tokens == 3
        assert metrics.output_tokens == 12
        assert metrics.cache_creation_input_tokens == 4552
        assert metrics.cache_read_input_tokens == 11027
        assert metrics.total_cost_usd == 0.020567
        assert metrics.duration_ms == 1517
        assert metrics.duration_api_ms == 1421
        assert metrics.tokens_per_second > 0
        assert metrics.stop_reason == "end_turn"
        assert metrics.is_error is False
        assert metrics.is_stream is False
        assert metrics.ttft_ms is None
        assert metrics.num_turns == 1
        assert metrics.origin == "proxy"
        assert metrics.session_id == "50f85a80-7ef8-4ce9-8163-2c64db7e19e4"
        assert metrics.request_id == "msg_50f85a80-7ef8-4ce9-8163-2c64db7e19e4"
        assert metrics.timestamp  # not empty

    def test_streaming_with_ttft(self):
        event = _make_result_event()
        metrics = build_metrics_from_result(event, is_stream=True, ttft_ms=450)

        assert metrics.is_stream is True
        assert metrics.ttft_ms == 450

    def test_error_result_uses_fallback_model(self):
        event = _make_result_event(
            is_error=True,
            modelUsage={},
            usage={},
            total_cost_usd=0,
            duration_api_ms=0,
        )
        metrics = build_metrics_from_result(
            event, is_stream=False, fallback_model="sonnet",
        )

        assert metrics.model == "sonnet"
        assert metrics.is_error is True
        assert metrics.total_cost_usd == 0.0
        assert metrics.tokens_per_second == 0.0

    def test_null_cost_handled(self):
        event = _make_result_event(total_cost_usd=None)
        metrics = build_metrics_from_result(event, is_stream=False)
        assert metrics.total_cost_usd == 0.0

    def test_missing_session_id(self):
        event = _make_result_event()
        del event["session_id"]
        metrics = build_metrics_from_result(event, is_stream=False)
        assert metrics.request_id.startswith("msg_")
        assert len(metrics.request_id) > 4

    def test_benchmark_origin(self):
        event = _make_result_event()
        metrics = build_metrics_from_result(
            event, is_stream=False, origin="benchmark",
        )
        assert metrics.origin == "benchmark"

    def test_null_usage_fields_handled(self):
        event = _make_result_event(usage={
            "input_tokens": None,
            "output_tokens": None,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": None,
        })
        metrics = build_metrics_from_result(event, is_stream=False)
        assert metrics.input_tokens == 0
        assert metrics.output_tokens == 0
        assert metrics.cache_creation_input_tokens == 0
        assert metrics.cache_read_input_tokens == 0
