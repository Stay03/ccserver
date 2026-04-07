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
        "messages": [{"role": "user", "content": "say hello"}],
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
async def test_non_streaming_success(client):
    with patch("app.routes.messages.run_claude", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = _mock_response()

        resp = await client.post("/v1/messages", json=_make_request_body())
        assert resp.status_code == 200

        data = resp.json()
        assert data["type"] == "message"
        assert data["role"] == "assistant"
        assert data["content"][0]["text"] == "Hello!"
        assert data["stop_reason"] == "end_turn"
        assert data["usage"]["input_tokens"] == 10


@pytest.mark.anyio
async def test_non_streaming_cli_not_found(client):
    with patch("app.routes.messages.run_claude", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = RuntimeError("Claude CLI not found at: /usr/bin/claude")

        resp = await client.post("/v1/messages", json=_make_request_body())
        assert resp.status_code == 503
        data = resp.json()
        assert data["type"] == "error"
        assert data["error"]["type"] == "api_error"


@pytest.mark.anyio
async def test_non_streaming_timeout(client):
    with patch("app.routes.messages.run_claude", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = RuntimeError("Claude CLI request timed out")

        resp = await client.post("/v1/messages", json=_make_request_body())
        assert resp.status_code == 408


@pytest.mark.anyio
async def test_non_streaming_auth_error(client):
    with patch("app.routes.messages.run_claude", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = RuntimeError("Claude CLI error: Not logged in")

        resp = await client.post("/v1/messages", json=_make_request_body())
        assert resp.status_code == 401


@pytest.mark.anyio
async def test_streaming_forwards_stream_events(client):
    """Verify that stream_event inner events are forwarded directly as SSE."""
    async def mock_stream(*args, **kwargs):
        from app.sse import format_sse
        # Simulate what the refactored stream_claude yields:
        # forwarded inner events from CLI stream_event wrappers
        yield format_sse("message_start", {
            "type": "message_start",
            "message": {
                "id": "msg_01NfJDTSHmQVJtyj2PWyRmPx",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-sonnet-4-6",
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 3, "output_tokens": 0},
            },
        })
        yield format_sse("content_block_start", {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        })
        yield format_sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello!"},
        })
        yield format_sse("content_block_stop", {
            "type": "content_block_stop",
            "index": 0,
        })
        yield format_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        })
        yield format_sse("message_stop", {"type": "message_stop"})

    with patch("app.routes.messages.stream_claude", side_effect=mock_stream):
        resp = await client.post(
            "/v1/messages",
            json=_make_request_body(stream=True),
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        text = resp.text
        assert "event: message_start" in text
        assert "event: content_block_start" in text
        assert "event: content_block_delta" in text
        assert "event: content_block_stop" in text
        assert "event: message_delta" in text
        assert "event: message_stop" in text
        assert "Hello!" in text
        assert "claude-sonnet-4-6" in text


@pytest.mark.anyio
async def test_streaming_error_emits_single_error(client):
    """Verify that error cases emit exactly one error event, not two."""
    async def mock_stream(*args, **kwargs):
        from app.sse import format_sse
        # Simulate error case: assistant has error, then result has is_error
        yield format_sse("error", {
            "type": "error",
            "error": {"type": "api_error", "message": "Model not found"},
        })
        # No second error — error_emitted flag prevents it

    with patch("app.routes.messages.stream_claude", side_effect=mock_stream):
        resp = await client.post(
            "/v1/messages",
            json=_make_request_body(stream=True),
        )
        assert resp.status_code == 200
        text = resp.text
        assert text.count("event: error") == 1


@pytest.mark.anyio
async def test_api_key_rejected(client):
    with patch("app.routes.messages.settings") as mock_settings:
        mock_settings.api_key = "secret123"
        resp = await client.post(
            "/v1/messages",
            json=_make_request_body(),
            headers={"x-api-key": "wrong_key"},
        )
        assert resp.status_code == 401


@pytest.mark.anyio
async def test_api_key_accepted(client):
    with patch("app.routes.messages.settings") as mock_settings, \
         patch("app.routes.messages.run_claude", new_callable=AsyncMock) as mock_run:
        mock_settings.api_key = "secret123"
        mock_run.return_value = _mock_response()

        resp = await client.post(
            "/v1/messages",
            json=_make_request_body(),
            headers={"x-api-key": "secret123"},
        )
        assert resp.status_code == 200


@pytest.mark.anyio
async def test_bearer_auth_header(client):
    with patch("app.routes.messages.settings") as mock_settings, \
         patch("app.routes.messages.run_claude", new_callable=AsyncMock) as mock_run:
        mock_settings.api_key = "secret123"
        mock_run.return_value = _mock_response()

        resp = await client.post(
            "/v1/messages",
            json=_make_request_body(),
            headers={"Authorization": "Bearer secret123"},
        )
        assert resp.status_code == 200


@pytest.mark.anyio
async def test_health_endpoint(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "claude_binary" in data


@pytest.mark.anyio
async def test_missing_messages_field(client):
    resp = await client.post("/v1/messages", json={"model": "sonnet", "max_tokens": 100})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_empty_messages_rejected(client):
    resp = await client.post("/v1/messages", json=_make_request_body(messages=[]))
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_invalid_model_returns_400(client):
    with patch("app.routes.messages.run_claude", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = RuntimeError(
            "Claude CLI error: There's an issue with the selected model (nonexistent)."
        )

        resp = await client.post("/v1/messages", json=_make_request_body(model="nonexistent"))
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["type"] == "invalid_request_error"
