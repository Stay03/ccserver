# Phase 3: Benchmark Endpoint (Updated with all 9 fixes)

## Goal
Active performance testing — fire N concurrent real requests to measure tokens/second, latency, and cost.

## Depends on
Phase 1 (metrics capture).

## Key Design: `run_benchmark_request()` (Issues 1, 2, 3, 9)

Instead of modifying `stream_claude()` with `metrics_holder`, add a new standalone function:

```python
async def run_benchmark_request(
    prompt: str, model: str, max_tokens: int, stream: bool = False
) -> RequestMetrics:
```

This function:
- Builds CLI command internally (reuses `_build_command` infrastructure)
- **Non-streaming**: runs subprocess, parses result_event, builds metrics
- **Streaming**: runs subprocess, reads CLI output line-by-line, tracks TTFT from first `stream_event` with `content_block_delta`, builds metrics from `result` event. Does NOT yield SSE — just consumes internally.
- Sets `origin="benchmark"` on all metrics
- Inserts to DB, returns `RequestMetrics`
- On error: returns `RequestMetrics` with `is_error=True`, never raises

Benefits:
- `run_claude()` and `stream_claude()` completely untouched
- No `metrics_holder` parameter pollution
- Streaming benchmark doesn't parse SSE strings back
- `origin="benchmark"` set cleanly

---

## Files to Create

### `app/models/benchmark.py`

```python
from pydantic import BaseModel, Field

class BenchmarkRequest(BaseModel):
    prompt: str = "Say hello in one sentence."
    model: str = ""                     # defaults to settings.default_model
    concurrency: int = Field(1, ge=1)   # Issue 7: validated
    num_requests: int = Field(5, ge=1)  # Issue 7: validated
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
    wall_time_ms: int              # Issue 4: wall clock for entire benchmark
    avg_tokens_per_second: float   # Issue 5: excludes failures
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
```

---

### `app/routes/benchmark.py`

```python
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

    # Build per-request results
    results = []
    for r in results_raw:
        if isinstance(r, Exception):
            results.append(BenchmarkResult(
                request_id="error", duration_ms=0, tokens_per_second=0,
                input_tokens=0, output_tokens=0, cost_usd=0,
                is_error=True, error_message=str(r),
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
                error_message=None,
            ))

    # Compute summary (Issue 5: avg_tps excludes failures)
    successful = [r for r in results if not r.is_error]
    failed = [r for r in results if r.is_error]
    success_tps = [r.tokens_per_second for r in successful if r.tokens_per_second > 0]
    success_durations = sorted(r.duration_ms for r in successful)
    success_ttfts = [r.ttft_ms for r in successful if r.ttft_ms is not None]

    summary = BenchmarkSummary(
        total_requests=len(results),
        successful=len(successful),
        failed=len(failed),
        total_cost_usd=round(sum(r.cost_usd for r in results), 6),
        wall_time_ms=wall_time_ms,
        avg_tokens_per_second=round(sum(success_tps)/len(success_tps), 1) if success_tps else 0.0,
        avg_duration_ms=round(sum(success_durations)/len(success_durations)) if success_durations else 0,
        avg_ttft_ms=round(sum(success_ttfts)/len(success_ttfts)) if success_ttfts else None,
        p50_duration_ms=_percentile(success_durations, 0.50),
        p95_duration_ms=_percentile(success_durations, 0.95),
        min_tps=round(min(success_tps), 1) if success_tps else 0.0,
        max_tps=round(max(success_tps), 1) if success_tps else 0.0,
    )

    total_cost = summary.total_cost_usd
    return BenchmarkResponse(
        summary=summary,
        requests=results,
        warning=f"This benchmark consumed real API tokens. Total cost: ${total_cost:.4f}",
    )
```

---

## Files to Modify

### `app/services/claude_cli.py` — add `run_benchmark_request()`

New function only. Existing functions untouched.

```python
async def run_benchmark_request(
    prompt: str, model: str, max_tokens: int, stream: bool = False
) -> RequestMetrics:
    """Run a single benchmark request. Returns metrics directly, never raises."""
    request = MessagesRequest(
        model=model,
        messages=[Message(role="user", content=prompt)],
        max_tokens=max_tokens,
        stream=stream,
    )
    cmd = _build_command(request, streaming=stream)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return RequestMetrics(
            request_id=f"msg_{uuid.uuid4().hex}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            model=model, is_error=True, origin="benchmark",
        )

    try:
        if stream:
            return await _benchmark_stream(proc, model)
        else:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=settings.request_timeout
            )
            output = stdout.decode(errors="replace").strip()
            if not output:
                # empty output error
                ...return error metrics...
            result_event = json.loads(output)
            metrics = build_metrics_from_result(
                result_event, is_stream=False, fallback_model=model, origin="benchmark",
            )
            await _insert_metrics(metrics)
            return metrics
    except asyncio.TimeoutError:
        proc.kill()
        return ...error metrics with is_error=True...
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def _benchmark_stream(proc, model) -> RequestMetrics:
    """Consume streaming CLI output for benchmark. Track TTFT, return metrics."""
    start_time = time.monotonic()
    ttft_ms = None
    result_event = None

    async for raw_line in proc.stdout:
        line = raw_line.decode(errors="replace").strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if event.get("type") == "stream_event":
            inner = event.get("event", {})
            if inner.get("type") == "content_block_delta" and ttft_ms is None:
                ttft_ms = int((time.monotonic() - start_time) * 1000)
        elif event.get("type") == "result":
            result_event = event

    if result_event:
        metrics = build_metrics_from_result(
            result_event, is_stream=True, fallback_model=model,
            ttft_ms=ttft_ms, origin="benchmark",
        )
        await _insert_metrics(metrics)
        return metrics
    
    # No result event — error
    return ...error metrics...
```

### `app/config.py`
```python
benchmark_max_concurrency: int = 10
benchmark_max_requests: int = 50
```

### `app/main.py`
Register: `app.include_router(benchmark_router)`

### `app/models/metrics.py`
Add `origin` parameter to `build_metrics_from_result` — **already exists** from Phase 1. No change needed.

---

## Verification
1. `python -m pytest tests/ -v` — all pass
2. Non-streaming benchmark:
   ```bash
   curl -X POST https://claude.lawexa.com/v1/benchmark \
     -H "Content-Type: application/json" \
     -d '{"prompt":"say hello","num_requests":3,"concurrency":2}'
   ```
3. Streaming benchmark: add `"stream": true`
4. Check logs: `curl "https://claude.lawexa.com/v1/logs?origin=benchmark"`
5. Validation: `concurrency=0` → 422
6. Concurrency cap: `concurrency=100` → silently capped to 10
7. Warning shows total cost
