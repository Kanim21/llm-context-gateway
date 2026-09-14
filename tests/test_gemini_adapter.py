"""Tests for OpenAI<->Gemini translation (agent_gateway.adapters.gemini_adapter)."""

from __future__ import annotations

import json

import pytest

from agent_gateway.adapters.gemini_adapter import _sanitize_json_schema, translate_request


class TestTranslateRequestTextTurns:
    def test_system_message_becomes_top_level_system_instruction(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hi"},
        ]}
        result = translate_request(body)
        assert result["systemInstruction"] == {"parts": [{"text": "You are a helpful assistant."}]}
        assert all(c.get("role") != "system" for c in result["contents"])

    def test_no_system_message_omits_system_instruction(self):
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "Hi"}]}
        result = translate_request(body)
        assert "systemInstruction" not in result

    def test_user_turn_maps_to_role_user(self):
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "Hello"}]}
        result = translate_request(body)
        assert result["contents"] == [{"role": "user", "parts": [{"text": "Hello"}]}]

    def test_assistant_turn_maps_to_role_model(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]}
        result = translate_request(body)
        assert result["contents"][1] == {"role": "model", "parts": [{"text": "Hi there"}]}

    def test_multiple_system_messages_join_with_newline(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "system", "content": "Rule 1."},
            {"role": "system", "content": "Rule 2."},
            {"role": "user", "content": "Hi"},
        ]}
        result = translate_request(body)
        assert result["systemInstruction"] == {"parts": [{"text": "Rule 1.\nRule 2."}]}


class TestTranslateRequestToolCalls:
    def test_assistant_tool_call_turn_becomes_function_call_part(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "user", "content": "What's the weather in Paris?"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}},
            ]},
        ]}
        result = translate_request(body)
        assert result["contents"][1] == {
            "role": "model",
            "parts": [{"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}}],
        }

    def test_tool_result_turn_recovers_name_from_same_request_history(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "user", "content": "What's the weather in Paris?"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{"temp_c": 18}'},
        ]}
        result = translate_request(body)
        assert result["contents"][2] == {
            "role": "user",
            "parts": [{"functionResponse": {"name": "get_weather", "response": {"temp_c": 18}}}],
        }

    def test_tool_result_with_bare_string_content_is_wrapped_in_result_object(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "ping", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "pong"},
        ]}
        result = translate_request(body)
        response = result["contents"][1]["parts"][0]["functionResponse"]["response"]
        assert response == {"result": "pong"}

    def test_tool_result_with_json_array_content_is_wrapped_in_result_object(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "list_items", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "[1, 2, 3]"},
        ]}
        result = translate_request(body)
        response = result["contents"][1]["parts"][0]["functionResponse"]["response"]
        assert response == {"result": [1, 2, 3]}

    def test_tool_result_with_json_object_content_passes_through_unwrapped(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{"status": "ok"}'},
        ]}
        result = translate_request(body)
        response = result["contents"][1]["parts"][0]["functionResponse"]["response"]
        assert response == {"status": "ok"}

    def test_tools_map_to_function_declarations_with_sanitized_schema(self):
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {
                    "name": "get_weather", "description": "Get weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                                   "additionalProperties": False, "$schema": "http://json-schema.org/draft-07/schema#"},
                }}]}
        result = translate_request(body)
        decl = result["tools"][0]["functionDeclarations"][0]
        assert decl["name"] == "get_weather"
        assert "additionalProperties" not in decl["parameters"]
        assert "$schema" not in decl["parameters"]

    @pytest.mark.parametrize("tool_choice,expected", [
        ("none", {"functionCallingConfig": {"mode": "NONE"}}),
        ("auto", {"functionCallingConfig": {"mode": "AUTO"}}),
        ("required", {"functionCallingConfig": {"mode": "ANY"}}),
        ({"type": "function", "function": {"name": "get_weather"}},
         {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["get_weather"]}}),
    ])
    def test_tool_choice_mapping(self, tool_choice, expected):
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}],
                "tool_choice": tool_choice}
        result = translate_request(body)
        assert result["toolConfig"] == expected


class TestSanitizeJsonSchema:
    def test_strips_additional_properties_and_schema_key_recursively(self):
        schema = {
            "type": "object",
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#",
            "properties": {
                "nested": {"type": "object", "additionalProperties": False, "properties": {}},
            },
        }
        result = _sanitize_json_schema(schema)
        assert "additionalProperties" not in result
        assert "$schema" not in result
        assert "additionalProperties" not in result["properties"]["nested"]

    def test_strips_default_only_at_root(self):
        schema = {
            "type": "object", "default": {},
            "properties": {"count": {"type": "integer", "default": 0}},
        }
        result = _sanitize_json_schema(schema)
        assert "default" not in result
        assert result["properties"]["count"]["default"] == 0


from agent_gateway.adapters.gemini_adapter import translate_response


class TestTranslateResponse:
    def test_text_only_response(self):
        gemini_response = {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "Hello there"}]},
                             "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15},
        }
        result = translate_response(gemini_response, model="gemini-1.5-pro")
        assert result["object"] == "chat.completion"
        assert result["model"] == "gemini-1.5-pro"
        assert result["choices"][0]["message"]["content"] == "Hello there"
        assert result["choices"][0]["finish_reason"] == "stop"
        assert result["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    def test_single_function_call_response(self):
        gemini_response = {
            "candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}},
            ]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
        }
        result = translate_response(gemini_response, model="gemini-1.5-pro")
        tool_calls = result["choices"][0]["message"]["tool_calls"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["function"]["name"] == "get_weather"
        assert json.loads(tool_calls[0]["function"]["arguments"]) == {"city": "Paris"}
        assert tool_calls[0]["id"].startswith("call_gemini_")
        assert result["choices"][0]["finish_reason"] == "tool_calls"

    def test_parallel_function_calls_response(self):
        gemini_response = {
            "candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}},
                {"functionCall": {"name": "get_time", "args": {"city": "Paris"}}},
            ]}, "finishReason": "STOP"}],
            "usageMetadata": {},
        }
        result = translate_response(gemini_response, model="gemini-1.5-pro")
        assert len(result["choices"][0]["message"]["tool_calls"]) == 2

    def test_max_tokens_beats_tool_calls_for_finish_reason(self):
        gemini_response = {
            "candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "get_weather", "args": {}}},
            ]}, "finishReason": "MAX_TOKENS"}],
            "usageMetadata": {},
        }
        result = translate_response(gemini_response, model="gemini-1.5-pro")
        assert result["choices"][0]["finish_reason"] == "length"

    @pytest.mark.parametrize("gemini_reason,expected", [
        ("SAFETY", "content_filter"),
        ("RECITATION", "content_filter"),
        ("STOP", "stop"),
        ("OTHER", "stop"),
    ])
    def test_finish_reason_mapping_without_function_call(self, gemini_reason, expected):
        gemini_response = {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "hi"}]},
                             "finishReason": gemini_reason}],
            "usageMetadata": {},
        }
        result = translate_response(gemini_response, model="gemini-1.5-pro")
        assert result["choices"][0]["finish_reason"] == expected

    def test_missing_usage_metadata_defaults_to_zero(self):
        gemini_response = {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "hi"}]}, "finishReason": "STOP"}],
        }
        result = translate_response(gemini_response, model="gemini-1.5-pro")
        assert result["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
