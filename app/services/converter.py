from __future__ import annotations

import uuid

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
    if cli_stop_reason in ("stop_sequence", None):
        return "end_turn"
    if cli_stop_reason == "max_tokens":
        return "max_tokens"
    return "end_turn"


def parse_cli_result(result_event: dict, model: str) -> MessagesResponse:
    session_id = result_event.get("session_id", "")
    msg_id = f"msg_{session_id[:24]}" if session_id else f"msg_{uuid.uuid4().hex[:24]}"
    result_text = result_event.get("result", "")
    usage_data = result_event.get("usage", {})
    stop_reason = map_stop_reason(result_event.get("stop_reason"))

    return MessagesResponse(
        id=msg_id,
        model=model,
        content=[ContentBlock(type="text", text=result_text)],
        stop_reason=stop_reason,
        usage=Usage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
        ),
    )
