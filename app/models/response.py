from __future__ import annotations

from pydantic import BaseModel


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class ContentBlock(BaseModel):
    type: str = "text"
    text: str = ""


class MessagesResponse(BaseModel):
    id: str = ""
    type: str = "message"
    role: str = "assistant"
    content: list[ContentBlock] = []
    model: str = ""
    stop_reason: str | None = "end_turn"
    stop_sequence: str | None = None
    usage: Usage = Usage()


class ErrorDetail(BaseModel):
    type: str = "api_error"
    message: str = ""


class ErrorResponse(BaseModel):
    type: str = "error"
    error: ErrorDetail
