from __future__ import annotations

from pydantic import BaseModel


class OpenAIMessage(BaseModel):
    role: str
    content: str


class OpenAIChatRequest(BaseModel):
    model: str = ""
    messages: list[OpenAIMessage]
    temperature: float | None = None
    max_tokens: int = 4096
    stream: bool = False


class OpenAIChoice(BaseModel):
    index: int = 0
    message: OpenAIMessage
    finish_reason: str = "stop"


class OpenAIStreamDelta(BaseModel):
    role: str | None = None
    content: str | None = None


class OpenAIStreamChoice(BaseModel):
    index: int = 0
    delta: OpenAIStreamDelta
    finish_reason: str | None = None


class OpenAIUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class OpenAIChatResponse(BaseModel):
    id: str = ""
    object: str = "chat.completion"
    model: str = ""
    choices: list[OpenAIChoice] = []
    usage: OpenAIUsage = OpenAIUsage()


class OpenAIStreamChunk(BaseModel):
    id: str = ""
    object: str = "chat.completion.chunk"
    model: str = ""
    choices: list[OpenAIStreamChoice] = []
