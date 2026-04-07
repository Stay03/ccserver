from __future__ import annotations

from pydantic import BaseModel, Field


class BenchmarkRequest(BaseModel):
    prompt: str = "Say hello in one sentence."
    model: str = ""
    concurrency: int = Field(1, ge=1)
    num_requests: int = Field(5, ge=1)
    stream: bool = False
    max_tokens: int = 256


class BenchmarkResult(BaseModel):
    request_id: str
    duration_ms: int
    tokens_per_second: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    ttft_ms: int | None = None
    is_error: bool
    error_message: str | None = None


class BenchmarkSummary(BaseModel):
    total_requests: int
    successful: int
    failed: int
    total_cost_usd: float
    wall_time_ms: int
    avg_tokens_per_second: float
    avg_duration_ms: float
    avg_ttft_ms: float | None = None
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    min_tps: float
    max_tps: float


class BenchmarkResponse(BaseModel):
    summary: BenchmarkSummary
    requests: list[BenchmarkResult]
    warning: str
