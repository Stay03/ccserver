from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator

from app.config import settings
from app.models.request import MessagesRequest
from app.models.response import MessagesResponse
from app.services.converter import (
    extract_system_text,
    map_stop_reason,
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

    if result_event.get("is_error"):
        error_msg = result_event.get("result", "Unknown CLI error")
        raise RuntimeError(f"Claude CLI error: {error_msg}")

    return parse_cli_result(result_event, model)


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

    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    previous_text = ""
    content_block_started = False

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

            if event_type == "system":
                cli_model = event.get("model", model)
                yield format_sse("message_start", {
                    "type": "message_start",
                    "message": {
                        "id": msg_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": cli_model,
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                })

            elif event_type == "assistant":
                message = event.get("message", {})
                error = event.get("error")

                if error:
                    yield format_sse("error", {
                        "type": "error",
                        "error": {"type": "authentication_error", "message": error},
                    })
                    continue

                content_blocks = message.get("content", [])
                current_text = ""
                for block in content_blocks:
                    if block.get("type") == "text":
                        current_text += block.get("text", "")

                delta = current_text[len(previous_text):]
                if delta:
                    if not content_block_started:
                        yield format_sse("content_block_start", {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": ""},
                        })
                        content_block_started = True

                    yield format_sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": delta},
                    })
                    previous_text = current_text

            elif event_type == "result":
                if event.get("is_error"):
                    error_msg = event.get("result", "Unknown error")
                    if not content_block_started:
                        yield format_sse("content_block_start", {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": ""},
                        })
                        content_block_started = True
                    yield format_sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": error_msg},
                    })

                if content_block_started:
                    yield format_sse("content_block_stop", {
                        "type": "content_block_stop",
                        "index": 0,
                    })

                usage = event.get("usage", {})
                stop_reason = map_stop_reason(event.get("stop_reason"))

                yield format_sse("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": usage.get("output_tokens", 0)},
                })

                yield format_sse("message_stop", {"type": "message_stop"})

    except asyncio.TimeoutError:
        yield format_sse("error", {
            "type": "error",
            "error": {"type": "api_error", "message": "Request timed out"},
        })
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
