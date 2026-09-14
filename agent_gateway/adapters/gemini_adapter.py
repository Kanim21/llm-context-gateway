"""Bidirectional translation between OpenAI's /v1/chat/completions wire
shape and Google Gemini's generateContent/streamGenerateContent shape,
plus the I/O adapter that calls Gemini.

All translation functions are pure (no I/O) so they're unit-testable
directly. Field names use Gemini's actual documented REST casing
(systemInstruction, toolConfig, functionCallingConfig,
allowedFunctionNames, functionDeclarations, functionCall,
functionResponse) throughout.
"""

from __future__ import annotations

from typing import Any


def translate_request(body: dict[str, Any]) -> dict[str, Any]:
    """Translates an OpenAI-shaped /v1/chat/completions body into a
    Gemini generateContent-shaped body."""
    messages = body.get("messages", [])
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")
        if role == "system":
            system_parts.append(msg.get("content") or "")
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": msg.get("content") or ""}]})
        else:  # "user"
            contents.append({"role": "user", "parts": [{"text": msg.get("content") or ""}]})

    gemini_body: dict[str, Any] = {"contents": contents}
    if system_parts:
        gemini_body["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}

    return gemini_body
