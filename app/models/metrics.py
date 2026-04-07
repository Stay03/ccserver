from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timezone


def compute_tps(output_tokens: int, duration_api_ms: int) -> float:
    if output_tokens <= 0 or duration_api_ms <= 0:
        return 0.0
    return output_tokens / (duration_api_ms / 1000)


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


def build_metrics_from_result(
    result_event: dict,
    is_stream: bool,
    fallback_model: str = "unknown",
    ttft_ms: int | None = None,
    origin: str = "proxy",
) -> RequestMetrics:
    from app.services.converter import resolve_model

    session_id = result_event.get("session_id", "")
    usage = result_event.get("usage", {})
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    duration_api_ms = result_event.get("duration_api_ms", 0) or 0

    model = resolve_model(result_event, fallback_model)

    return RequestMetrics(
        request_id=f"msg_{session_id}" if session_id else f"msg_{uuid.uuid4().hex}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=usage.get("cache_read_input_tokens", 0) or 0,
        total_cost_usd=result_event.get("total_cost_usd") or 0.0,
        duration_ms=result_event.get("duration_ms", 0) or 0,
        duration_api_ms=duration_api_ms,
        ttft_ms=ttft_ms,
        tokens_per_second=compute_tps(output_tokens, duration_api_ms),
        stop_reason=result_event.get("stop_reason"),
        is_error=bool(result_event.get("is_error", False)),
        is_stream=is_stream,
        num_turns=result_event.get("num_turns", 1) or 1,
        origin=origin,
        session_id=session_id,
    )
