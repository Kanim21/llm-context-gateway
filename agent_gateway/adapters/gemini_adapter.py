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

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx


def _extract_text(content: Any) -> str:
    """OpenAI allows `content` to be a plain string or a list of content
    parts (used for vision/multipart messages). Flattens to plain text by
    concatenating every {"type": "text", "text": ...} part; non-text parts
    (e.g. image_url) are dropped -- Gemini image support is out of scope
    for this plan."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def translate_request(body: dict[str, Any]) -> dict[str, Any]:
    """Translates an OpenAI-shaped /v1/chat/completions body into a
    Gemini generateContent-shaped body."""
    messages = body.get("messages", [])
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")
        if role == "system":
            system_parts.append(_extract_text(msg.get("content")))
        elif role == "tool":
            contents.append(_translate_tool_result_turn(msg, messages))
        elif role == "assistant" and msg.get("tool_calls"):
            contents.append(_translate_assistant_tool_call_turn(msg))
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": _extract_text(msg.get("content"))}]})
        else:  # "user"
            contents.append({"role": "user", "parts": [{"text": _extract_text(msg.get("content"))}]})

    gemini_body: dict[str, Any] = {"contents": contents}
    if system_parts:
        gemini_body["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}

    tools = body.get("tools")
    if tools:
        gemini_body["tools"] = [{
            "functionDeclarations": [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "parameters": _sanitize_json_schema(t["function"].get("parameters", {})),
                }
                for t in tools if t.get("type") == "function"
            ]
        }]

    if body.get("tool_choice") is not None:
        gemini_body["toolConfig"] = _translate_tool_choice(body["tool_choice"])

    generation_config: dict[str, Any] = {}
    if body.get("temperature") is not None:
        generation_config["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        generation_config["topP"] = body["top_p"]
    if body.get("max_tokens") is not None:
        generation_config["maxOutputTokens"] = body["max_tokens"]
    if body.get("stop") is not None:
        stop = body["stop"]
        generation_config["stopSequences"] = stop if isinstance(stop, list) else [stop]
    if generation_config:
        gemini_body["generationConfig"] = generation_config

    return gemini_body


def _translate_assistant_tool_call_turn(msg: dict[str, Any]) -> dict[str, Any]:
    parts = [
        {"functionCall": {"name": call["function"]["name"],
                           "args": json.loads(call["function"].get("arguments") or "{}")}}
        for call in msg["tool_calls"]
    ]
    return {"role": "model", "parts": parts}


def _translate_tool_result_turn(msg: dict[str, Any], all_messages: list[dict[str, Any]]) -> dict[str, Any]:
    name = _lookup_tool_call_name(msg["tool_call_id"], all_messages)
    response = _wrap_tool_response(msg.get("content", ""))
    return {"role": "user", "parts": [{"functionResponse": {"name": name, "response": response}}]}


def _lookup_tool_call_name(tool_call_id: str, all_messages: list[dict[str, Any]]) -> str:
    """Recovers the function name for a tool result by scanning the same
    request's own message history -- OpenAI resends full history every
    call, so no persistent tool-call registry is needed."""
    for msg in all_messages:
        for call in msg.get("tool_calls") or []:
            if call.get("id") == tool_call_id:
                return call["function"]["name"]
    raise ValueError(f"No assistant tool_calls entry found for tool_call_id={tool_call_id!r}")


def _wrap_tool_response(content: Any) -> dict[str, Any]:
    """Gemini requires functionResponse.response to be a JSON object.
    Non-object tool output (a bare string, JSON array/scalar, or
    unparseable text) is wrapped as {"result": content}."""
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return {"result": content}
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    return {"result": content}


def _sanitize_json_schema(schema: dict[str, Any], *, is_root: bool = True) -> dict[str, Any]:
    """Recursively strips JSON Schema keywords Gemini's function-calling
    schema rejects: `additionalProperties` and `$schema` at every level,
    and `default` at the root level only."""
    if not isinstance(schema, dict):
        return schema

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in ("additionalProperties", "$schema"):
            continue
        if key == "default" and is_root:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: _sanitize_json_schema(v, is_root=False) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            out[key] = _sanitize_json_schema(value, is_root=False)
        else:
            out[key] = value
    return out


def _translate_tool_choice(tool_choice: str | dict[str, Any]) -> dict[str, Any]:
    if tool_choice == "none":
        return {"functionCallingConfig": {"mode": "NONE"}}
    if tool_choice == "required":
        return {"functionCallingConfig": {"mode": "ANY"}}
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        name = tool_choice["function"]["name"]
        return {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [name]}}
    return {"functionCallingConfig": {"mode": "AUTO"}}


def translate_response(gemini_response: dict[str, Any], model: str) -> dict[str, Any]:
    """Translates a Gemini generateContent response body into an
    OpenAI-chat-completions-shaped response body. A prompt-level block
    (HTTP 200 with promptFeedback.blockReason and no candidates) is
    translated to a content_filter completion with no message content,
    rather than raising."""
    usage = gemini_response.get("usageMetadata", {})
    usage_out = {
        "prompt_tokens": usage.get("promptTokenCount", 0),
        "completion_tokens": usage.get("candidatesTokenCount", 0),
        "total_tokens": usage.get("totalTokenCount", 0),
    }

    candidates = gemini_response.get("candidates") or []
    if not candidates:
        return {
            "id": f"chatcmpl-gemini-{uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": None},
                "finish_reason": "content_filter",
            }],
            "usage": usage_out,
        }

    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])

    text_parts = [p["text"] for p in parts if "text" in p]
    function_calls = [p["functionCall"] for p in parts if "functionCall" in p]

    message: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
    if function_calls:
        message["tool_calls"] = [
            {
                "id": f"call_gemini_{uuid4().hex}",
                "type": "function",
                "function": {"name": fc["name"], "arguments": json.dumps(fc.get("args", {}))},
            }
            for fc in function_calls
        ]

    finish_reason = _translate_finish_reason(candidate.get("finishReason"), bool(function_calls))

    return {
        "id": f"chatcmpl-gemini-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage_out,
    }


def _translate_finish_reason(gemini_reason: str | None, has_function_call: bool) -> str:
    """MAX_TOKENS outranks tool_calls: a truncated response reports
    "length" even if a partial function call is present."""
    if gemini_reason == "MAX_TOKENS":
        return "length"
    if has_function_call:
        return "tool_calls"
    if gemini_reason in ("SAFETY", "RECITATION"):
        return "content_filter"
    return "stop"


def translate_stream_chunk(
    event: dict[str, Any], *, chunk_id: str, model: str, created: int
) -> list[dict[str, Any]]:
    """Translates one Gemini streamGenerateContent SSE JSON event into
    0+ OpenAI-shaped chat.completion.chunk dicts."""
    candidates = event.get("candidates") or []
    if not candidates:
        return []
    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])

    chunks: list[dict[str, Any]] = []

    text = "".join(p["text"] for p in parts if "text" in p)
    if text:
        chunks.append(_stream_chunk(chunk_id, model, created, delta={"content": text}))

    function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
    for i, fc in enumerate(function_calls):
        chunks.append(_stream_chunk(chunk_id, model, created, delta={
            "tool_calls": [{
                "index": i,
                "id": f"call_gemini_{uuid4().hex}",
                "type": "function",
                "function": {"name": fc["name"], "arguments": json.dumps(fc.get("args", {}))},
            }],
        }))

    finish_reason_raw = candidate.get("finishReason")
    if finish_reason_raw:
        finish_reason = _translate_finish_reason(finish_reason_raw, bool(function_calls))
        chunks.append(_stream_chunk(chunk_id, model, created, delta={}, finish_reason=finish_reason))

    return chunks


def _stream_chunk(
    chunk_id: str, model: str, created: int, *, delta: dict[str, Any], finish_reason: str | None = None
) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


@dataclass
class GeminiAdapter:
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    api_key: str | None = None
    timeout_s: float = 60.0
    api_key_header: str = "x-goog-api-key"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers[self.api_key_header] = self.api_key
        return headers

    async def generate_content(self, body: dict[str, Any], client: httpx.AsyncClient | None = None) -> dict[str, Any]:
        gemini_body = translate_request(body)
        owns_client = client is None
        client = client or httpx.AsyncClient(timeout=self.timeout_s)
        try:
            resp = await client.post(
                f"{self.base_url}/models/{quote(body['model'], safe='')}:generateContent",
                json=gemini_body,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()
        finally:
            if owns_client:
                await client.aclose()

    async def stream_generate_content(self, body: dict[str, Any], client: httpx.AsyncClient) -> AsyncIterator[bytes]:
        """Opens the upstream Gemini stream, checks its status eagerly
        (so an upstream error raises here rather than after the client
        has already received a 200), then returns an async generator
        that lazily translates and yields OpenAI-shaped SSE bytes."""
        gemini_body = translate_request(body)
        model = body["model"]
        url = f"{self.base_url}/models/{quote(model, safe='')}:streamGenerateContent?alt=sse"
        req = client.build_request("POST", url, json=gemini_body, headers=self._headers())
        resp = await client.send(req, stream=True)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            await resp.aread()
            await resp.aclose()
            raise

        chunk_id = f"chatcmpl-gemini-{uuid4().hex}"
        created = int(time.time())

        async def _iter() -> AsyncIterator[bytes]:
            try:
                async for event in _iter_sse_json_events(resp):
                    for oa_chunk in translate_stream_chunk(event, chunk_id=chunk_id, model=model, created=created):
                        yield f"data: {json.dumps(oa_chunk)}\n\n".encode()
                yield b"data: [DONE]\n\n"
            finally:
                await resp.aclose()

        return _iter()


async def _iter_sse_json_events(resp: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Parses a Gemini `alt=sse` response body into decoded JSON event
    dicts, one per `data: ` line."""
    buffer_lines: list[str] = []
    async for line in resp.aiter_lines():
        if line == "":
            if buffer_lines:
                payload = "\n".join(buffer_lines)
                buffer_lines = []
                yield json.loads(payload.removeprefix("data: "))
            continue
        buffer_lines.append(line)
    if buffer_lines:
        yield json.loads("\n".join(buffer_lines).removeprefix("data: "))
