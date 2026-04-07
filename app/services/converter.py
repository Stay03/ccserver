from __future__ import annotations

import uuid

from app.models.metrics import RequestMetrics, build_metrics_from_result
from app.models.request import Message, MessagesRequest, SystemBlock
from app.models.response import ContentBlock, MessagesResponse, Usage


def extract_text_from_content(content: str | list) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block["text"])
        elif hasattr(block, "type") and block.type == "text":
            parts.append(block.text)
    return "\n".join(parts)


def extract_system_text(system: str | list[SystemBlock] | None) -> str:
    if system is None:
        return ""
    if isinstance(system, str):
        return system
    parts = []
    for block in system:
        if isinstance(block, dict):
            parts.append(block.get("text", ""))
        else:
            parts.append(block.text)
    return "\n".join(parts)


def messages_to_prompt(messages: list[Message]) -> str:
    if len(messages) == 1 and messages[0].role == "user":
        return extract_text_from_content(messages[0].content)

    parts = []
    for msg in messages:
        label = "Human" if msg.role == "user" else "Assistant"
        text = extract_text_from_content(msg.content)
        parts.append(f"[{label}]: {text}")
    return "\n\n".join(parts)


def map_stop_reason(cli_stop_reason: str | None) -> str:
    if cli_stop_reason is None:
        return "end_turn"
    return cli_stop_reason


def resolve_model(result_event: dict, fallback: str) -> str:
    model_usage = result_event.get("modelUsage", {})
    if model_usage:
        return next(iter(model_usage))
    return fallback


def parse_cli_result(result_event: dict, model: str) -> tuple[MessagesResponse, RequestMetrics]:
    resolved = resolve_model(result_event, model)
    session_id = result_event.get("session_id", "")
    msg_id = f"msg_{session_id}" if session_id else f"msg_{uuid.uuid4().hex}"
    result_text = result_event.get("result", "")
    usage_data = result_event.get("usage", {})
    stop_reason = map_stop_reason(result_event.get("stop_reason"))

    response = MessagesResponse(
        id=msg_id,
        model=resolved,
        content=[ContentBlock(type="text", text=result_text)],
        stop_reason=stop_reason,
        usage=Usage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            cache_creation_input_tokens=usage_data.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=usage_data.get("cache_read_input_tokens", 0),
        ),
    )

    metrics = build_metrics_from_result(
        result_event, is_stream=False, fallback_model=model,
    )

    return response, metrics
