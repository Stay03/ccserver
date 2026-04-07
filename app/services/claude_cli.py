from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from app.config import settings
from app.models.metrics import build_metrics_from_result
from app.models.request import MessagesRequest
from app.models.response import MessagesResponse
from app.services.converter import (
    extract_system_text,
    messages_to_prompt,
    parse_cli_result,
)
from app.sse import format_sse

logger = logging.getLogger(__name__)


def _build_command(request: MessagesRequest, streaming: bool) -> list[str]:
    prompt = messages_to_prompt(request.messages)
    model = request.model or settings.default_model
    cmd = [
        settings.get_claude_path(),
        "-p",
        prompt,
        "--output-format",
        "stream-json" if streaming else "json",
        "--model",
        model,
        "--no-session-persistence",
    ]
    if streaming:
        cmd.append("--verbose")
        cmd.append("--include-partial-messages")

    system_text = extract_system_text(request.system)
    if system_text:
        cmd.extend(["--system-prompt", system_text])

    if settings.max_budget_usd is not None:
        cmd.extend(["--max-budget-usd", str(settings.max_budget_usd)])

    return cmd


async def run_claude(request: MessagesRequest) -> MessagesResponse:
    model = request.model or settings.default_model
    cmd = _build_command(request, streaming=False)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise RuntimeError(f"Claude CLI not found at: {settings.get_claude_path()}")

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=settings.request_timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("Claude CLI request timed out")

    if stderr:
        logger.warning("Claude CLI stderr: %s", stderr.decode(errors="replace"))

    output = stdout.decode(errors="replace").strip()
    if not output:
        raise RuntimeError("Claude CLI returned empty output")

    result_event = json.loads(output)

    # Issue C fix: log error requests to DB before raising
    if result_event.get("is_error"):
        metrics = build_metrics_from_result(
            result_event, is_stream=False, fallback_model=model,
        )
        await _insert_metrics(metrics)
        error_msg = result_event.get("result", "Unknown CLI error")
        raise RuntimeError(f"Claude CLI error: {error_msg}")

    response, metrics = parse_cli_result(result_event, model)
    await _insert_metrics(metrics)
    return response


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
    start_time = time.monotonic()
    ttft_ms: int | None = None
    first_delta_seen = False
    pending_metrics: build_metrics_from_result | None = None

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
                    # TTFT: timestamp first content delta
                    if not first_delta_seen and inner["type"] == "content_block_delta":
                        ttft_ms = int((time.monotonic() - start_time) * 1000)
                        first_delta_seen = True
                    yield format_sse(inner["type"], inner)

            elif event_type == "assistant":
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
                if event.get("is_error") and not error_emitted:
                    yield format_sse("error", {
                        "type": "error",
                        "error": {"type": "api_error", "message": event.get("result", "Unknown error")},
                    })

                # Build metrics to insert in finally block (Issue B fix)
                pending_metrics = build_metrics_from_result(
                    event,
                    is_stream=True,
                    fallback_model=model,
                    ttft_ms=ttft_ms,
                )

            elif event_type in ("system", "rate_limit_event"):
                pass

    except asyncio.TimeoutError:
        yield format_sse("error", {
            "type": "error",
            "error": {"type": "api_error", "message": "Request timed out"},
        })
    finally:
        # Issue B fix: insert metrics in finally so it runs even on client disconnect
        if pending_metrics is not None:
            await _insert_metrics(pending_metrics)
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def _insert_metrics(metrics) -> None:
    """Insert metrics to database. Import here to avoid circular imports at module level."""
    try:
        from app import database
        await database.insert_request_log(metrics)
    except Exception:
        logger.exception("Failed to log request metrics")
