# Phase 0: Fix Streaming + Quick Pre-Fixes

## Problem
The CLI already emits proper Anthropic-format SSE events as `stream_event` wrappers, but our code ignores them. Instead, it:
1. Creates a **synthetic** `message_start` from the `system` event (missing real usage data)
2. **Diffs accumulated text** from `assistant` events to compute deltas (hacky, lossy)
3. **Manually constructs** `content_block_stop`, `message_delta`, `message_stop` from the `result` event

Also includes quick pre-fixes for issues found during review.

## Required flags (verified from live testing)

| Flags | Events emitted |
|-------|---------------|
| `--verbose` only | `system`, `assistant`, `rate_limit_event`, `result` |
| `--verbose --include-partial-messages` | `system`, **`stream_event`** (all SSE), `assistant`, `rate_limit_event`, `result` |
| Neither | Empty output |

**Both `--verbose` AND `--include-partial-messages` are required** for `stream_event` events to appear.

## What the CLI emits with both flags (confirmed)

```
1. {"type":"system", ...}                                            → SKIP (metadata only)
2. {"type":"stream_event","event":{"type":"message_start",...}}       → FORWARD
3. {"type":"stream_event","event":{"type":"content_block_start",...}} → FORWARD
4. {"type":"stream_event","event":{"type":"content_block_delta",...}} → FORWARD (repeated)
5. {"type":"assistant","message":{...}}                               → SKIP (redundant snapshot)
6. {"type":"stream_event","event":{"type":"content_block_stop",...}}  → FORWARD
7. {"type":"stream_event","event":{"type":"message_delta",...}}       → FORWARD (usage + stop_reason)
8. {"type":"stream_event","event":{"type":"message_stop"}}           → FORWARD
9. {"type":"rate_limit_event",...}                                    → SKIP
10. {"type":"result",...}                                             → metrics only (Phase 1)
```

## Error case (verified: invalid model)
When an error occurs, **no `stream_event` events** are emitted. Only:
- `system` (init)
- `assistant` with `"error": "invalid_request"` and `model: "<synthetic>"`
- `result` with `"is_error": true`

---

## Changes

### 1. `app/services/claude_cli.py`

#### `_build_command()` changes:
- Add `--no-session-persistence` (prevents 12K+ session files/month accumulating on disk)
- Keep `--verbose` and `--include-partial-messages` for streaming

#### `stream_claude()` — full rewrite:

```python
async def stream_claude(request: MessagesRequest) -> AsyncGenerator[str, None]:
    model = request.model or settings.default_model
    cmd = _build_command(request, streaming=True)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        yield format_sse("error", {
            "type": "error",
            "error": {"type": "api_error", "message": f"Claude CLI not found at: {settings.get_claude_path()}"},
        })
        return

    error_emitted = False

    try:
        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Non-JSON line from CLI: %s", line[:200])
                continue

            event_type = event.get("type")

            if event_type == "stream_event":
                inner = event.get("event")
                if inner and "type" in inner:
                    yield format_sse(inner["type"], inner)

            elif event_type == "assistant":
                # Only relevant in error cases (no stream_events emitted)
                error = event.get("error")
                if error:
                    msg = event.get("message", {})
                    content = msg.get("content", [])
                    error_text = content[0].get("text", "") if content else str(error)
                    yield format_sse("error", {
                        "type": "error",
                        "error": {"type": "api_error", "message": error_text},
                    })
                    error_emitted = True

            elif event_type == "result":
                # Emit error ONLY if not already emitted from assistant event
                if event.get("is_error") and not error_emitted:
                    yield format_sse("error", {
                        "type": "error",
                        "error": {"type": "api_error", "message": event.get("result", "Unknown error")},
                    })
                # Phase 1 will add metrics capture here

            elif event_type in ("system", "rate_limit_event"):
                pass

    except asyncio.TimeoutError:
        yield format_sse("error", {
            "type": "error",
            "error": {"type": "api_error", "message": "Request timed out"},
        })
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
```

Key fixes vs original plan:
- **Issue 1 fixed**: `error_emitted` flag prevents double error emission
- **Issue 2 fixed**: `event.get("event")` with `if inner and "type" in inner` safety check
- **Issue 4 fixed**: `finally` block with process cleanup preserved
- **Issue 5 fixed**: `FileNotFoundError` try/except preserved

### 2. `app/services/converter.py`

#### Simplify `map_stop_reason()`:
CLI returns `"end_turn"` directly (verified). Simplify to pass-through with only `None` handling:

```python
def map_stop_reason(cli_stop_reason: str | None) -> str:
    if cli_stop_reason is None:
        return "end_turn"
    return cli_stop_reason
```

#### Extract resolved model from `modelUsage`:
```python
def resolve_model(result_event: dict, fallback: str) -> str:
    model_usage = result_event.get("modelUsage", {})
    if model_usage:
        return next(iter(model_usage))  # first key is the resolved model name
    return fallback
```

Use in `parse_cli_result()`: `model=resolve_model(result_event, model)`

### 3. `tests/test_api.py`

- Update `test_streaming_returns_sse` mock to yield forwarded `stream_event` inner events
- Add `test_streaming_error_no_double_emit` — verify only one error event on failure
- Add `test_streaming_cli_not_found` — verify FileNotFoundError handling

### 4. `tests/test_converter.py`

- Update `test_stop_sequence_maps_to_end_turn` → `test_end_turn_passes_through`
- Update test data to use `"stop_reason": "end_turn"` (matching real CLI output)
- Update `test_successful_result` to verify resolved model name

---

## Verification
1. `python -m pytest tests/ -v` — all pass
2. Deploy to droplet, test streaming:
   ```bash
   curl -X POST https://claude.lawexa.com/v1/messages \
     -H "Content-Type: application/json" \
     -d '{"model":"sonnet","messages":[{"role":"user","content":"say hello"}],"max_tokens":100,"stream":true}'
   ```
3. Verify SSE events have real model ID (`claude-sonnet-4-6`), real message ID, real usage
4. Test error: send `"model":"nonexistent"` — should get single error event, not hang
5. Check `~/.claude/sessions/` on droplet — no new session files created
