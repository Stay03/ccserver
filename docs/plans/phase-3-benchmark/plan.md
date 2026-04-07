# Phase 3: Benchmark Endpoint

## Goal
Active performance testing — fire N concurrent real requests to measure tokens/second, latency, and cost.

## Depends on
Phase 1 (metrics capture). Independent of Phase 2.

## Files to Create

### `app/models/benchmark.py`

**BenchmarkRequest:**
```python
class BenchmarkRequest(BaseModel):
    prompt: str = "Say hello in one sentence."
    model: str = ""                # defaults to settings.default_model
    concurrency: int = 1           # parallel CLI processes
    num_requests: int = 5          # total requests to fire
    stream: bool = False           # test streaming mode
    max_tokens: int = 256
```

**BenchmarkResult** (per-request):
```python
class BenchmarkResult(BaseModel):
    request_id: str
    duration_ms: int
    tokens_per_second: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    ttft_ms: int | None
    is_error: bool
    error_message: str | None = None
```

**BenchmarkSummary:**
```python
class BenchmarkSummary(BaseModel):
    total_requests: int
    successful: int
    failed: int
    total_cost_usd: float
    total_duration_ms: int
    avg_tokens_per_second: float
    avg_duration_ms: float
    avg_ttft_ms: float | None
    p50_duration_ms: int
    p95_duration_ms: int
    min_tps: float
    max_tps: float
```

**BenchmarkResponse:**
```python
class BenchmarkResponse(BaseModel):
    summary: BenchmarkSummary
    requests: list[BenchmarkResult]
    warning: str
```

---

### `app/routes/benchmark.py`

**`POST /v1/benchmark`**

Flow:
1. Validate request, cap concurrency and num_requests to config limits
2. Create `asyncio.Semaphore(concurrency)`
3. Launch `num_requests` tasks with semaphore limiting parallelism
4. Each task calls `run_claude()` (or consumes `stream_claude()` for streaming benchmarks)
5. Collect `RequestMetrics` from each task
6. Compute summary: avg/p50/p95 duration, avg TPS, total cost
7. Return `BenchmarkResponse` with warning about token cost

**Concurrency pattern:**
```python
semaphore = asyncio.Semaphore(body.concurrency)

async def _run_one():
    async with semaphore:
        return await _execute_benchmark_request(body)

tasks = [asyncio.create_task(_run_one()) for _ in range(body.num_requests)]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

**Streaming benchmark:** After Phase 0 fix, `stream_claude` forwards proper `stream_event` events. Consumes the generator internally to measure TTFT:
```python
async def _consume_stream(request):
    start = time.monotonic()
    ttft = None
    async for chunk in stream_claude(request, metrics_holder=holder):
        if ttft is None and "content_block_delta" in chunk:
            ttft = (time.monotonic() - start) * 1000
    return ttft, holder[0]  # metrics from the holder
```
TTFT is now more accurate since we're using real `stream_event` deltas, not reconstructed ones.

**All benchmark requests are logged with `origin="benchmark"`** so they can be filtered out of production stats or viewed separately.

---

## Files to Modify

### `app/services/claude_cli.py`
Add optional `metrics_holder: list | None = None` param to `stream_claude()`. If provided, append `RequestMetrics` to the list instead of fire-and-forget inserting. Default behavior unchanged.

### `app/config.py`
Add:
```python
benchmark_max_concurrency: int = 10
benchmark_max_requests: int = 50
```

### `app/main.py`
Register benchmark router: `app.include_router(benchmark_router)`

---

## Verification
1. Basic test:
   ```bash
   curl -X POST https://claude.lawexa.com/v1/benchmark \
     -H "Content-Type: application/json" \
     -d '{"prompt":"say hello","num_requests":3,"concurrency":2}'
   ```
2. Verify response has summary with TPS, latency, cost
3. Verify warning about real token consumption
4. Check `/v1/logs?origin=benchmark` shows the 3 benchmark requests
5. Test streaming benchmark: add `"stream": true`
6. Test concurrency cap: try `concurrency: 100`, should be capped to config max
