from app.models.request import Message, TextContent
from app.services.converter import (
    extract_system_text,
    extract_text_from_content,
    map_stop_reason,
    messages_to_prompt,
    parse_cli_result,
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
    def test_stop_sequence_maps_to_end_turn(self):
        assert map_stop_reason("stop_sequence") == "end_turn"

    def test_none_maps_to_end_turn(self):
        assert map_stop_reason(None) == "end_turn"

    def test_max_tokens_passes_through(self):
        assert map_stop_reason("max_tokens") == "max_tokens"

    def test_unknown_maps_to_end_turn(self):
        assert map_stop_reason("something_else") == "end_turn"


class TestParseCliResult:
    def test_successful_result(self):
        event = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Hello! How can I help?",
            "stop_reason": "stop_sequence",
            "session_id": "abc123def456789012345678",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        response = parse_cli_result(event, "sonnet")
        assert response.model == "sonnet"
        assert response.role == "assistant"
        assert response.type == "message"
        assert len(response.content) == 1
        assert response.content[0].text == "Hello! How can I help?"
        assert response.stop_reason == "end_turn"
        assert response.usage.input_tokens == 10
        assert response.usage.output_tokens == 5
        assert response.id.startswith("msg_")

    def test_result_with_no_usage(self):
        event = {"type": "result", "result": "hi", "session_id": "x"}
        response = parse_cli_result(event, "opus")
        assert response.usage.input_tokens == 0
        assert response.usage.output_tokens == 0

    def test_result_with_no_session_id(self):
        event = {"type": "result", "result": "hi"}
        response = parse_cli_result(event, "haiku")
        assert response.id.startswith("msg_")
