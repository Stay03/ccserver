from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.models.openai import (
    OpenAIChatRequest,
    OpenAIChatResponse,
    OpenAIChoice,
    OpenAIMessage,
    OpenAIStreamChunk,
    OpenAIStreamChoice,
    OpenAIStreamDelta,
    OpenAIUsage,
)
from app.models.request import Message, MessagesRequest
from app.services.claude_cli import run_claude, stream_claude

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_api_key(api_key: str | None) -> None:
    if settings.api_key and api_key != settings.api_key:
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Invalid API key", "type": "invalid_api_key"}},
        )


def _openai_to_anthropic(request: OpenAIChatRequest) -> MessagesRequest:
    system_text = None
    messages = []
    for msg in request.messages:
        if msg.role == "system":
            system_text = msg.content
        else:
            messages.append(Message(role=msg.role, content=msg.content))

    if not messages:
        raise HTTPException(status_code=422, detail="No user/assistant messages provided")

    return MessagesRequest(
        model=request.model,
        messages=messages,
        system=system_text,
        max_tokens=request.max_tokens,
        stream=request.stream,
    )


def _anthropic_stop_to_openai(stop_reason: str | None) -> str:
    if stop_reason == "max_tokens":
        return "length"
    return "stop"


@router.post("/v1/chat/completions")
async def chat_completions(
    request: OpenAIChatRequest,
    authorization: str | None = Header(None),
):
    key = None
    if authorization and authorization.startswith("Bearer "):
        key = authorization[7:]
    _check_api_key(key)

    anthropic_request = _openai_to_anthropic(request)

    if request.stream:
        return StreamingResponse(
            _stream_openai(anthropic_request, request.model),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        response = await run_claude(anthropic_request)

        text = ""
        for block in response.content:
            if block.type == "text":
                text += block.text

        openai_response = OpenAIChatResponse(
            id=response.id,
            model=response.model,
            choices=[
                OpenAIChoice(
                    message=OpenAIMessage(role="assistant", content=text),
                    finish_reason=_anthropic_stop_to_openai(response.stop_reason),
                )
            ],
            usage=OpenAIUsage(
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
                total_tokens=response.usage.input_tokens + response.usage.output_tokens,
            ),
        )
        return JSONResponse(content=openai_response.model_dump())

    except RuntimeError as e:
        error_msg = str(e)
        logger.error("Claude CLI error: %s", error_msg)
        if "not found" in error_msg.lower():
            status = 503
        elif "timed out" in error_msg.lower():
            status = 408
        elif "logged in" in error_msg.lower() or "login" in error_msg.lower():
            status = 401
        elif "model" in error_msg.lower():
            status = 400
        else:
            status = 500

        return JSONResponse(
            status_code=status,
            content={"error": {"message": error_msg, "type": "server_error"}},
        )


async def _stream_openai(anthropic_request: MessagesRequest, requested_model: str):
    """Convert Anthropic SSE stream to OpenAI SSE format."""
    msg_id = ""
    model = requested_model

    async for sse_line in stream_claude(anthropic_request):
        # Parse the SSE line: "event: <type>\ndata: <json>\n\n"
        if not sse_line.strip():
            continue

        lines = sse_line.strip().split("\n")
        event_type = ""
        data_str = ""
        for line in lines:
            if line.startswith("event: "):
                event_type = line[7:]
            elif line.startswith("data: "):
                data_str = line[6:]

        if not data_str:
            continue

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        if event_type == "message_start":
            message = data.get("message", {})
            msg_id = message.get("id", "")
            model = message.get("model", requested_model)
            chunk = OpenAIStreamChunk(
                id=msg_id,
                model=model,
                choices=[OpenAIStreamChoice(
                    delta=OpenAIStreamDelta(role="assistant"),
                )],
            )
            yield f"data: {json.dumps(chunk.model_dump())}\n\n"

        elif event_type == "content_block_delta":
            delta = data.get("delta", {})
            text = delta.get("text", "")
            if text:
                chunk = OpenAIStreamChunk(
                    id=msg_id,
                    model=model,
                    choices=[OpenAIStreamChoice(
                        delta=OpenAIStreamDelta(content=text),
                    )],
                )
                yield f"data: {json.dumps(chunk.model_dump())}\n\n"

        elif event_type == "message_delta":
            delta = data.get("delta", {})
            stop_reason = delta.get("stop_reason")
            chunk = OpenAIStreamChunk(
                id=msg_id,
                model=model,
                choices=[OpenAIStreamChoice(
                    delta=OpenAIStreamDelta(),
                    finish_reason=_anthropic_stop_to_openai(stop_reason),
                )],
            )
            yield f"data: {json.dumps(chunk.model_dump())}\n\n"

        elif event_type == "message_stop":
            yield "data: [DONE]\n\n"

        elif event_type == "error":
            error = data.get("error", {})
            yield f"data: {json.dumps({'error': error})}\n\n"
            yield "data: [DONE]\n\n"
