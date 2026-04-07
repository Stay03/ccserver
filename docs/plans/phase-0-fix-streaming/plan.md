# Phase 0: Fix Streaming Implementation

## Problem
The CLI already emits proper Anthropic-format SSE events as `stream_event` wrappers, but our code ignores them. Instead, it:
1. Creates a **synthetic** `message_start` from the `system` event (missing real usage data)
2. **Diffs accumulated text** from `assistant` events to compute deltas (hacky, lossy)
3. **Manually constructs** `content_block_stop`, `message_delta`, `message_stop` from the `result` event

The correct approach: just **forward `stream_event.event` directly** as SSE.

## Required flags (verified from live testing)

| Flags | Events emitted |
|-------|---------------|
| `--verbose` only | `system`, `assistant`, `rate_limit_event`, `result` |
| `--verbose --include-partial-messages` | `system`, **`stream_event`** (all SSE), `assistant`, `rate_limit_event`, `result` |
| Neither | Empty output |

**Both `--verbose` AND `--include-partial-messages` are required** for `stream_event` events to appear.

## What the CLI emits with both flags (confirmed)

```
1. {"type":"system", ...}                                          → metadata only, SKIP
2. {"type":"stream_event","event":{"type":"message_start",...}}     → FORWARD
3. {"type":"stream_event","event":{"type":"content_block_start",...}} → FORWARD
4. {"type":"stream_event","event":{"type":"content_block_delta",...}} → FORWARD (repeated)
5. {"type":"assistant","message":{...}}                             → SKIP (redundant snapshot)
6. {"type":"stream_event","event":{"type":"content_block_stop",...}} → FORWARD
7. {"type":"stream_event","event":{"type":"message_delta",...}}     → FORWARD (usage + stop_reason)
8. {"type":"stream_event","event":{"type":"message_stop"}}         → FORWARD
9. {"type":"rate_limit_event",...}                                  → SKIP/LOG
10. {"type":"result",...}                                           → metrics only (Phase 1)
```

## Error case (verified: invalid model)
When an error occurs, **no `stream_event` events** are emitted. Only:
- `system` (init)
- `assistant` with `"error": "invalid_request"` and `model: "<synthetic>"`
- `result` with `"is_error": true`

This means the error path must still handle the `assistant` event for error messages, or rely on the `result` event's `is_error` flag.

## Changes

### `app/services/claude_cli.py` — Rewrite `stream_claude()`

**Before (current — 100+ lines of manual reconstruction):**
- Handles: `system`, `assistant`, `result`
- Ignores: `stream_event`, `rate_limit_event`
- Manually builds SSE events from `assistant` text diffs

**After (clean — ~50 lines of forwarding):**
```python
async def stream_claude(request):
    cmd = _build_command(request, streaming=True)
    proc = await asyncio.create_subprocess_exec(...)

    async for raw_line in proc.stdout:
        line = raw_line.decode(errors="replace").strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type")

        if event_type == "stream_event":
            # Forward the inner Anthropic event directly as SSE
            inner = event["event"]
            yield format_sse(inner["type"], inner)

        elif event_type == "assistant":
            # In error cases, stream_event events don't appear.
            # Check for error field and emit error SSE.
            error = event.get("error")
            if error:
                msg = event.get("message", {})
                content = msg.get("content", [])
                error_text = content[0].get("text", "") if content else str(error)
                yield format_sse("error", {
                    "type": "error",
                    "error": {"type": "api_error", "message": error_text},
                })

        elif event_type == "result":
            # If is_error and no stream_events were emitted, emit error
            if event.get("is_error"):
                yield format_sse("error", {
                    "type": "error",
                    "error": {"type": "api_error", "message": event.get("result", "Unknown error")},
                })
            # Phase 1 will add metrics capture here

        elif event_type in ("system", "rate_limit_event"):
            # system: metadata only, message_start comes via stream_event
            # rate_limit_event: informational, don't forward
            pass
```

**Keep in `_build_command()`:**
- Keep `--verbose` (required)
- Keep `--include-partial-messages` (required for `stream_event` events)

### `tests/test_api.py` — Update streaming test

Update `test_streaming_returns_sse` to mock forwarding `stream_event` inner events.

Add tests:
- `test_streaming_error_invalid_model` — error case with no stream_events
- `test_streaming_forwards_real_events` — verify inner events are forwarded directly

## What this fixes
- Proper `message_start` with real model name, message ID, and initial usage from the API
- Proper text deltas (no more string diffing)
- Proper `message_delta` with accurate usage and stop_reason from the API
- Simpler, more maintainable code

## What this enables for later phases
- **Phase 1 TTFT**: Timestamp when first `stream_event` with inner `content_block_delta` is forwarded
- **Phase 1 metrics**: `result` event handler is clean place to capture cost/duration
- **Phase 3 benchmark**: Cleaner streaming consumption for benchmark measurement

## Verification
1. Run tests: `python -m pytest tests/ -v`
2. Deploy to droplet, test streaming:
   ```bash
   curl -X POST https://claude.lawexa.com/v1/messages \
     -H "Content-Type: application/json" \
     -d '{"model":"sonnet","messages":[{"role":"user","content":"say hello"}],"max_tokens":100,"stream":true}'
   ```
3. Verify SSE events match Anthropic format (message_start has real model/id from API, not synthetic)
4. Test error case — should get error SSE event, not hang
