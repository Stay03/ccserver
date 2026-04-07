from app.models.request import Message, TextContent
from app.services.converter import (
    extract_system_text,
    extract_text_from_content,
    map_stop_reason,
    messages_to_prompt,
    parse_cli_result,
    resolve_model,
)


class TestExtractTextFromContent:
    def test_string_content(self):
        assert extract_text_from_content("hello world") == "hello world"

    def test_list_of_text_blocks(self):
        blocks = [
            TextContent(type="text", text="hello"),
            TextContent(type="text", text="world"),
        ]
        assert extract_text_from_content(blocks) == "hello\nworld"

    def test_list_of_dicts(self):
        blocks = [
            {"type": "text", "text": "foo"},
            {"type": "text", "text": "bar"},
        ]
        assert extract_text_from_content(blocks) == "foo\nbar"

    def test_mixed_types_skips_non_text(self):
        blocks = [
            {"type": "text", "text": "keep"},
            {"type": "image", "source": {}},
        ]
        assert extract_text_from_content(blocks) == "keep"

    def test_empty_list(self):
        assert extract_text_from_content([]) == ""


class TestExtractSystemText:
    def test_none(self):
        assert extract_system_text(None) == ""

    def test_string(self):
        assert extract_system_text("be helpful") == "be helpful"

    def test_list_of_dicts(self):
        blocks = [{"type": "text", "text": "rule 1"}, {"type": "text", "text": "rule 2"}]
        assert extract_system_text(blocks) == "rule 1\nrule 2"


class TestMessagesToPrompt:
    def test_single_user_message_returns_raw_text(self):
        messages = [Message(role="user", content="hello")]
        assert messages_to_prompt(messages) == "hello"

    def test_multi_turn_conversation(self):
        messages = [
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello"),
            Message(role="user", content="how are you?"),
        ]
        result = messages_to_prompt(messages)
        assert "[Human]: hi" in result
        assert "[Assistant]: hello" in result
        assert "[Human]: how are you?" in result

    def test_single_user_with_content_blocks(self):
        messages = [
            Message(role="user", content=[TextContent(type="text", text="test prompt")])
        ]
        assert messages_to_prompt(messages) == "test prompt"


class TestMapStopReason:
    def test_none_maps_to_end_turn(self):
        assert map_stop_reason(None) == "end_turn"

    def test_end_turn_passes_through(self):
        assert map_stop_reason("end_turn") == "end_turn"

    def test_max_tokens_passes_through(self):
        assert map_stop_reason("max_tokens") == "max_tokens"

    def test_any_value_passes_through(self):
        assert map_stop_reason("stop_sequence") == "stop_sequence"


class TestResolveModel:
    def test_resolves_from_model_usage(self):
        event = {"modelUsage": {"claude-sonnet-4-6": {"inputTokens": 3}}}
        assert resolve_model(event, "sonnet") == "claude-sonnet-4-6"

    def test_falls_back_when_empty(self):
        event = {"modelUsage": {}}
        assert resolve_model(event, "sonnet") == "sonnet"

    def test_falls_back_when_missing(self):
        event = {}
        assert resolve_model(event, "opus") == "opus"


def _make_cli_result(**overrides):
    """Build a realistic CLI result event matching confirmed live output."""
    event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Hello! How can I help?",
        "stop_reason": "end_turn",
        "session_id": "50f85a80-7ef8-4ce9-8163-2c64db7e19e4",
        "total_cost_usd": 0.020567,
        "duration_ms": 1517,
        "duration_api_ms": 1421,
        "num_turns": 1,
        "usage": {
            "input_tokens": 3,
            "output_tokens": 12,
            "cache_creation_input_tokens": 4552,
            "cache_read_input_tokens": 11027,
        },
        "modelUsage": {
            "claude-sonnet-4-6": {
                "inputTokens": 3,
                "outputTokens": 12,
                "costUSD": 0.020567,
            }
        },
    }
    event.update(overrides)
    return event


class TestParseCliResult:
    def test_successful_result(self):
        event = _make_cli_result()
        response, metrics = parse_cli_result(event, "sonnet")

        # Response assertions
        assert response.model == "claude-sonnet-4-6"
        assert response.role == "assistant"
        assert response.type == "message"
        assert len(response.content) == 1
        assert response.content[0].text == "Hello! How can I help?"
        assert response.stop_reason == "end_turn"
        assert response.usage.input_tokens == 3
        assert response.usage.output_tokens == 12
        assert response.usage.cache_creation_input_tokens == 4552
        assert response.usage.cache_read_input_tokens == 11027
        assert response.id == "msg_50f85a80-7ef8-4ce9-8163-2c64db7e19e4"

        # Metrics assertions
        assert metrics.model == "claude-sonnet-4-6"
        assert metrics.total_cost_usd == 0.020567
        assert metrics.duration_ms == 1517
        assert metrics.duration_api_ms == 1421
        assert metrics.tokens_per_second > 0
        assert metrics.input_tokens == 3
        assert metrics.output_tokens == 12
        assert metrics.cache_creation_input_tokens == 4552
        assert metrics.cache_read_input_tokens == 11027
        assert metrics.is_stream is False
        assert metrics.is_error is False
        assert metrics.num_turns == 1
        assert metrics.session_id == "50f85a80-7ef8-4ce9-8163-2c64db7e19e4"

    def test_result_with_no_usage(self):
        event = _make_cli_result(usage={}, modelUsage={})
        response, metrics = parse_cli_result(event, "opus")
        assert response.usage.input_tokens == 0
        assert response.usage.output_tokens == 0
        assert response.model == "opus"
        assert metrics.model == "opus"
        assert metrics.tokens_per_second == 0.0

    def test_result_with_no_session_id(self):
        event = _make_cli_result(session_id="")
        del event["session_id"]
        response, metrics = parse_cli_result(event, "haiku")
        assert response.id.startswith("msg_")
        assert metrics.request_id.startswith("msg_")

    def test_error_result(self):
        event = _make_cli_result(
            is_error=True,
            result="Not logged in",
            total_cost_usd=0,
            duration_api_ms=0,
            modelUsage={},
            usage={},
        )
        response, metrics = parse_cli_result(event, "sonnet")
        assert metrics.is_error is True
        assert metrics.total_cost_usd == 0.0
        assert metrics.tokens_per_second == 0.0
        assert metrics.model == "sonnet"

    def test_null_cost_handled(self):
        event = _make_cli_result(total_cost_usd=None)
        _, metrics = parse_cli_result(event, "sonnet")
        assert metrics.total_cost_usd == 0.0
