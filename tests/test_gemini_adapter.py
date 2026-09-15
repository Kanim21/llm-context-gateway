"""Tests for OpenAI<->Gemini translation (agent_gateway.adapters.gemini_adapter)."""

from __future__ import annotations

import json

import httpx
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

    def test_array_form_content_is_flattened_to_text(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "hello"},
                {"type": "text", "text": " world"},
            ]},
        ]}
        result = translate_request(body)
        assert result["contents"][0]["parts"][0]["text"] == "hello world"

    def test_plain_string_content_still_works(self):
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hello"}]}
        result = translate_request(body)
        assert result["contents"][0]["parts"][0]["text"] == "hello"


class TestTranslateRequestGenerationConfig:
    def test_generation_params_map_to_generation_config(self):
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}],
                "temperature": 0.2, "max_tokens": 128, "top_p": 0.9, "stop": ["\n"]}
        result = translate_request(body)
        assert result["generationConfig"] == {
            "temperature": 0.2, "topP": 0.9, "maxOutputTokens": 128, "stopSequences": ["\n"],
        }

    def test_no_generation_params_omits_generation_config(self):
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}]}
        result = translate_request(body)
        assert "generationConfig" not in result


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

    def test_prompt_level_safety_block_with_no_candidates_does_not_raise(self):
        gemini_response = {"promptFeedback": {"blockReason": "SAFETY"}}
        result = translate_response(gemini_response, model="gemini-1.5-pro")
        assert result["choices"][0]["finish_reason"] == "content_filter"
        assert result["choices"][0]["message"]["content"] is None


from agent_gateway.adapters.gemini_adapter import translate_stream_chunk


class TestTranslateStreamChunk:
    CHUNK_ID = "chatcmpl-gemini-test"
    MODEL = "gemini-1.5-pro"
    CREATED = 1_700_000_000

    def test_text_delta_chunk(self):
        event = {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]}
        chunks = translate_stream_chunk(event, chunk_id=self.CHUNK_ID, model=self.MODEL, created=self.CREATED)
        assert len(chunks) == 1
        assert chunks[0]["object"] == "chat.completion.chunk"
        assert chunks[0]["choices"][0]["delta"] == {"content": "Hel"}
        assert chunks[0]["choices"][0]["finish_reason"] is None

    def test_function_call_delta_chunk(self):
        event = {"candidates": [{"content": {"parts": [
            {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}},
        ]}}]}
        chunks = translate_stream_chunk(event, chunk_id=self.CHUNK_ID, model=self.MODEL, created=self.CREATED)
        assert len(chunks) == 1
        delta = chunks[0]["choices"][0]["delta"]
        assert delta["tool_calls"][0]["function"]["name"] == "get_weather"
        assert json.loads(delta["tool_calls"][0]["function"]["arguments"]) == {"city": "Paris"}

    def test_terminal_chunk_carries_finish_reason(self):
        event = {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]}
        chunks = translate_stream_chunk(event, chunk_id=self.CHUNK_ID, model=self.MODEL, created=self.CREATED)
        assert len(chunks) == 1
        assert chunks[0]["choices"][0]["finish_reason"] == "stop"
        assert chunks[0]["choices"][0]["delta"] == {}

    def test_event_with_no_candidates_produces_no_chunks(self):
        assert translate_stream_chunk({}, chunk_id=self.CHUNK_ID, model=self.MODEL, created=self.CREATED) == []

    def test_all_chunks_share_the_same_id_model_and_created(self):
        event = {"candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}]}
        chunks = translate_stream_chunk(event, chunk_id=self.CHUNK_ID, model=self.MODEL, created=self.CREATED)
        for chunk in chunks:
            assert chunk["id"] == self.CHUNK_ID
            assert chunk["model"] == self.MODEL
            assert chunk["created"] == self.CREATED


from agent_gateway.adapters.gemini_adapter import GeminiAdapter


class TestGeminiAdapterGenerateContent:
    async def test_generate_content_posts_translated_body_and_returns_raw_response(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}],
                "usageMetadata": {},
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GeminiAdapter(base_url="https://generativelanguage.googleapis.com/v1beta", api_key="gm-key")
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}]}

        result = await adapter.generate_content(body, client=client)

        assert "models/gemini-1.5-pro:generateContent" in captured["url"]
        assert captured["headers"]["x-goog-api-key"] == "gm-key"
        assert captured["body"]["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
        assert result["candidates"][0]["content"]["parts"][0]["text"] == "hi"

        await client.aclose()

    async def test_generate_content_respects_custom_api_key_header(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}],
                "usageMetadata": {},
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GeminiAdapter(base_url="https://generativelanguage.googleapis.com/v1beta",
                                 api_key="custom-key", api_key_header="X-Custom-Key")
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}]}

        await adapter.generate_content(body, client=client)

        assert captured["headers"]["x-custom-key"] == "custom-key"
        assert "x-goog-api-key" not in captured["headers"]

        await client.aclose()

    async def test_generate_content_raises_on_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GeminiAdapter(base_url="https://generativelanguage.googleapis.com/v1beta", api_key="bad-key")
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}]}

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.generate_content(body, client=client)

        await client.aclose()


class TestGeminiAdapterStreamGenerateContent:
    async def test_stream_generate_content_yields_translated_sse_ending_in_done(self):
        sse_body = (
            'data: {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]}\n\n'
            'data: {"candidates": [{"content": {"parts": [{"text": "lo"}]}, "finishReason": "STOP"}]}\n\n'
        ).encode()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse_body,
                                   headers={"content-type": "text/event-stream"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GeminiAdapter(base_url="https://generativelanguage.googleapis.com/v1beta", api_key="gm-key")
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}], "stream": True}

        gen = await adapter.stream_generate_content(body, client=client)
        raw_chunks = [chunk async for chunk in gen]
        text = b"".join(raw_chunks).decode()

        assert text.endswith("data: [DONE]\n\n")
        assert '"content": "Hel"' in text or '"content":"Hel"' in text
        # 3, not 2: the second Gemini SSE event bundles text ("lo") with
        # finishReason="STOP" in one payload, and the already-tested,
        # frozen translate_stream_chunk() (Tasks 4-7) correctly fans that
        # out into two separate OpenAI chunks -- a content delta and a
        # terminal empty-delta finish_reason chunk -- matching real
        # OpenAI streaming semantics. Combined with the first event's one
        # content-delta chunk, that's 3 total chat.completion.chunk
        # objects on the wire.
        assert text.count("chat.completion.chunk") == 3

        await client.aclose()

    async def test_stream_generate_content_raises_before_yielding_on_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GeminiAdapter(base_url="https://generativelanguage.googleapis.com/v1beta", api_key="bad-key")
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}], "stream": True}

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.stream_generate_content(body, client=client)

        await client.aclose()
