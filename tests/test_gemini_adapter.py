"""Tests for OpenAI<->Gemini translation (agent_gateway.adapters.gemini_adapter)."""

from __future__ import annotations

from agent_gateway.adapters.gemini_adapter import translate_request


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
