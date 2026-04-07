from __future__ import annotations

from pydantic import BaseModel, Field


class TextContent(BaseModel):
    type: str = "text"
    text: str


class ImageSource(BaseModel):
    type: str = "base64"
    media_type: str = ""
    data: str = ""


class ImageContent(BaseModel):
    type: str = "image"
    source: ImageSource


class Message(BaseModel):
    role: str
    content: str | list[TextContent | ImageContent | dict]


class SystemBlock(BaseModel):
    type: str = "text"
    text: str


class MessagesRequest(BaseModel):
    model: str = ""
    messages: list[Message] = Field(min_length=1)
    system: str | list[SystemBlock] | None = None
    max_tokens: int = 4096
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    stop_sequences: list[str] | None = None
    metadata: dict | None = None
