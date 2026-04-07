import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.response import ContentBlock, MessagesResponse, Usage


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _make_request_body(**overrides):
    body = {
        "model": "sonnet",
        "messages": [
            {"role": "user", "content": "say hello"},
        ],
        "max_tokens": 100,
    }
    body.update(overrides)
    return body


def _mock_response():
    return MessagesResponse(
        id="msg_test123",
        model="claude-sonnet-4-6",
        content=[ContentBlock(type="text", text="Hello!")],
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=5),
    )


@pytest.mark.anyio
async def test_basic_chat_completion(client):
    with patch("app.routes.chat_completions.run_claude", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = _mock_response()

        resp = await client.post("/v1/chat/completions", json=_make_request_body())
        assert resp.status_code == 200

        data = resp.json()
        assert data["object"] == "chat.completion"
        assert data["model"] == "claude-sonnet-4-6"
        assert len(data["choices"]) == 1
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert data["choices"][0]["message"]["content"] == "Hello!"
        assert data["choices"][0]["finish_reason"] == "stop"
        assert data["usage"]["prompt_tokens"] == 10
        assert data["usage"]["completion_tokens"] == 5
        assert data["usage"]["total_tokens"] == 15


@pytest.mark.anyio
async def test_system_message_extracted(client):
    with patch("app.routes.chat_completions.run_claude", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = _mock_response()

        body = _make_request_body(messages=[
            {"role": "system", "content": "You are a pirate."},
            {"role": "user", "content": "hello"},
        ])
        resp = await client.post("/v1/chat/completions", json=body)
        assert resp.status_code == 200

        # Verify system was extracted and passed to Anthropic format
        call_args = mock_run.call_args[0][0]
        assert call_args.system == "You are a pirate."
        assert len(call_args.messages) == 1
        assert call_args.messages[0].role == "user"


@pytest.mark.anyio
async def test_multi_turn_conversation(client):
    with patch("app.routes.chat_completions.run_claude", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = _mock_response()

        body = _make_request_body(messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "how are you?"},
        ])
        resp = await client.post("/v1/chat/completions", json=body)
        assert resp.status_code == 200

        call_args = mock_run.call_args[0][0]
        assert len(call_args.messages) == 3


@pytest.mark.anyio
async def test_max_tokens_stop_reason(client):
    with patch("app.routes.chat_completions.run_claude", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = MessagesResponse(
            id="msg_test",
            model="claude-sonnet-4-6",
            content=[ContentBlock(type="text", text="truncated")],
            stop_reason="max_tokens",
            usage=Usage(input_tokens=10, output_tokens=100),
        )

        resp = await client.post("/v1/chat/completions", json=_make_request_body())
        data = resp.json()
        assert data["choices"][0]["finish_reason"] == "length"


@pytest.mark.anyio
async def test_system_only_messages_rejected(client):
    body = _make_request_body(messages=[
        {"role": "system", "content": "be helpful"},
    ])
    resp = await client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_cli_not_found_error(client):
    with patch("app.routes.chat_completions.run_claude", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = RuntimeError("Claude CLI not found at: /usr/bin/claude")
        resp = await client.post("/v1/chat/completions", json=_make_request_body())
        assert resp.status_code == 503
        data = resp.json()
        assert "error" in data


@pytest.mark.anyio
async def test_invalid_model_error(client):
    with patch("app.routes.chat_completions.run_claude", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = RuntimeError("Claude CLI error: issue with model")
        resp = await client.post("/v1/chat/completions", json=_make_request_body())
        assert resp.status_code == 400


@pytest.mark.anyio
async def test_bearer_auth(client):
    with patch("app.routes.chat_completions.settings") as mock_settings, \
         patch("app.routes.chat_completions.run_claude", new_callable=AsyncMock) as mock_run:
        mock_settings.api_key = "secret123"
        mock_run.return_value = _mock_response()

        # Wrong key
        resp = await client.post(
            "/v1/chat/completions",
            json=_make_request_body(),
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401

        # Correct key
        resp = await client.post(
            "/v1/chat/completions",
            json=_make_request_body(),
            headers={"Authorization": "Bearer secret123"},
        )
        assert resp.status_code == 200


@pytest.mark.anyio
async def test_streaming_returns_sse(client):
    async def mock_stream(*args, **kwargs):
        from app.sse import format_sse
        yield format_sse("message_start", {
            "type": "message_start",
            "message": {"id": "msg_test", "model": "claude-sonnet-4-6", "role": "assistant",
                        "content": [], "stop_reason": None, "usage": {"input_tokens": 3}},
        })
        yield format_sse("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "Hello!"},
        })
        yield format_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 5},
        })
        yield format_sse("message_stop", {"type": "message_stop"})

    with patch("app.routes.chat_completions.stream_claude", side_effect=mock_stream):
        resp = await client.post(
            "/v1/chat/completions",
            json=_make_request_body(stream=True),
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        text = resp.text
        assert "data: " in text
        assert "Hello!" in text
        assert "data: [DONE]" in text
        # Verify it's OpenAI format, not Anthropic
        assert "chat.completion.chunk" in text
        assert "content_block_delta" not in text


@pytest.mark.anyio
async def test_empty_messages_rejected(client):
    resp = await client.post("/v1/chat/completions", json={
        "model": "sonnet",
        "messages": [],
        "max_tokens": 100,
    })
    assert resp.status_code == 422
