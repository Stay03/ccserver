from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter

from app.config import settings
from app.models.benchmark import (
    BenchmarkRequest,
    BenchmarkResponse,
    BenchmarkResult,
    BenchmarkSummary,
)
from app.services.claude_cli import run_benchmark_request

router = APIRouter()


def _percentile(sorted_values: list, pct: float) -> int | None:
    if not sorted_values:
        return None
    idx = int(len(sorted_values) * pct)
    idx = min(idx, len(sorted_values) - 1)
    return sorted_values[idx]


@router.post("/v1/benchmark")
async def run_benchmark(body: BenchmarkRequest):
    concurrency = min(body.concurrency, settings.benchmark_max_concurrency)
    num_requests = min(body.num_requests, settings.benchmark_max_requests)
    model = body.model or settings.default_model

    semaphore = asyncio.Semaphore(concurrency)
    wall_start = time.monotonic()

    async def _run_one():
        async with semaphore:
            return await run_benchmark_request(
                prompt=body.prompt,
                model=model,
                max_tokens=body.max_tokens,
                stream=body.stream,
            )

    tasks = [asyncio.create_task(_run_one()) for _ in range(num_requests)]
    results_raw = await asyncio.gather(*tasks, return_exceptions=True)

    wall_time_ms = int((time.monotonic() - wall_start) * 1000)

    results = []
    for r in results_raw:
        if isinstance(r, Exception):
            results.append(BenchmarkResult(
                request_id="error",
                duration_ms=0,
                tokens_per_second=0,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0,
                is_error=True,
                error_message=str(r),
            ))
        else:
            results.append(BenchmarkResult(
                request_id=r.request_id,
                duration_ms=r.duration_ms,
                tokens_per_second=r.tokens_per_second,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                cost_usd=r.total_cost_usd,
                ttft_ms=r.ttft_ms,
                is_error=r.is_error,
            ))

    successful = [r for r in results if not r.is_error]
    failed = [r for r in results if r.is_error]
    success_tps = [r.tokens_per_second for r in successful if r.tokens_per_second > 0]
    success_durations = sorted(r.duration_ms for r in successful)
    success_ttfts = [r.ttft_ms for r in successful if r.ttft_ms is not None]

    total_cost = round(sum(r.cost_usd for r in results), 6)

    summary = BenchmarkSummary(
        total_requests=len(results),
        successful=len(successful),
        failed=len(failed),
        total_cost_usd=total_cost,
        wall_time_ms=wall_time_ms,
        avg_tokens_per_second=round(sum(success_tps) / len(success_tps), 1) if success_tps else 0.0,
        avg_duration_ms=round(sum(success_durations) / len(success_durations)) if success_durations else 0,
        avg_ttft_ms=round(sum(success_ttfts) / len(success_ttfts)) if success_ttfts else None,
        p50_duration_ms=_percentile(success_durations, 0.50),
        p95_duration_ms=_percentile(success_durations, 0.95),
        min_tps=round(min(success_tps), 1) if success_tps else 0.0,
        max_tps=round(max(success_tps), 1) if success_tps else 0.0,
    )

    return BenchmarkResponse(
        summary=summary,
        requests=results,
        warning=f"This benchmark consumed real API tokens. Total cost: ${total_cost:.4f}",
    )
