# Task 9 Completion Report: OpenAI Streaming Adapter

## Summary
Implemented streaming support for OpenAIAdapter following the eager-connect I/O pattern. Added 4 new test cases and updated the adapter with the new `stream_chat_completions()` method and configurable `api_key_header` field.

## Files Modified

### 1. `agent_gateway/adapters/openai_adapter.py`
**Changes:**
- Added import: `from collections.abc import AsyncIterator`
- Added new dataclass field: `api_key_header: str = "Authorization"` (for custom API key header support)
- Refactored `_headers()` method to respect the `api_key_header` field:
  - If `api_key_header == "Authorization"`, use `Authorization: Bearer {api_key}` (existing behavior)
  - Otherwise, use the custom header name with the raw api_key value
- Added `stream_chat_completions(body: dict[str, Any], client: httpx.AsyncClient) -> AsyncIterator[bytes]` method:
  - Builds request using `client.build_request()` 
  - Sends with `stream=True` for raw byte streaming
  - Checks status eagerly via `resp.is_success` (before returning generator)
  - On error: reads full response, closes it, and raises via `raise_for_status()`
  - On success: returns async generator that yields chunks via `resp.aiter_bytes()`
  - Generator ensures proper cleanup with try/finally block
- Updated module docstring to note scope expansion: now serves DeepSeek and other OpenAI-compatible upstreams (Ollama/vLLM/llama.cpp/LM Studio)

**Line Count:** +32 lines added, 1 line modified (total 107 insertions)

### 2. `tests/test_openai_adapter_streaming.py` (NEW)
**TestStreamChatCompletions class with 4 test cases:**

1. `test_relays_sse_body_byte_for_byte`: Verifies SSE response is relayed byte-for-byte unchanged
2. `test_sends_authorization_header`: Confirms default `Authorization: Bearer` header is sent
3. `test_respects_custom_api_key_header`: Verifies custom `api_key_header` field works (e.g., `X-Custom-Key`)
4. `test_raises_before_yielding_on_http_error`: Ensures HTTP errors (401) raise immediately before returning generator, not mid-stream

**Coverage:** All critical streaming paths tested with httpx.MockTransport (no real network calls)

**Line Count:** 76 new lines

## Implementation Notes

### Eager-Connect Pattern
- Follows the exact pattern from GeminiAdapter (Task 8)
- Checks HTTP status code immediately after `client.send(req, stream=True)`
- Uses `resp.is_success` property (non-consuming status check)
- Only consumes response body on error path
- Preserves stream integrity for successful responses

### API Key Header Configuration
- Default value `"Authorization"` is backward compatible — existing code gets identical behavior
- `_headers()` method now conditionally formats the authorization:
  - Standard path: `{"Authorization": "Bearer {api_key}"}`
  - Custom path: `{api_key_header: api_key}`
- Verified by Step 4b: all 69 existing tests pass unchanged

### Streaming Mechanics
- Uses `aiter_bytes()` instead of `aiter_raw()` for MockTransport compatibility
- Async generator pattern with nested `_iter()` function ensures:
  - Status check happens before generator construction (eager-connect)
  - Response cleanup happens in finally block
  - Ownership model: caller passes in client, method owns generator lifecycle

## Test Results

### New Test File (test_openai_adapter_streaming.py)
```
PASSED: test_relays_sse_body_byte_for_byte
PASSED: test_sends_authorization_header
PASSED: test_respects_custom_api_key_header
PASSED: test_raises_before_yielding_on_http_error

4/4 tests passed (100%)
```

### Backward Compatibility Regression Suite (test_agent_gateway.py)
```
All 69 existing tests PASSED:
- TestCacheBoundary (7 tests)
- TestTokenizerEngine (5 tests)
- TestObservationMasking (8 tests)
- TestCompaction (7 tests)
- TestGuardrails (7 tests)
- TestBlackboard (4 tests)
- TestRouting (3 tests)
- TestLossyPasses (5 tests)
- TestToolPruning (3 tests)
- TestSemanticDedup (3 tests)
- TestBranchCollapse (1 test)
- TestZclawCodec (7 tests)
- TestSqliteStore (4 tests)
- TestUnblendedMetrics (3 tests)

69/69 tests passed (100%)
```

### Combined Test Run
```
73 total tests passed in 0.27s
- 4 new streaming tests
- 69 existing regression tests
```

## Commit Information

**Hash:** `45e40b8`

**Message:** 
```
feat: add OpenAIAdapter.stream_chat_completions for SSE passthrough

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
```

**Diff Stat:**
```
 agent_gateway/adapters/openai_adapter.py | 32 +++++++++++++-
 tests/test_openai_adapter_streaming.py   | 76 ++++++++++++++++++++++++++++++++
 2 files changed, 107 insertions(+), 1 deletion(-)
```

## Deviations from Brief

**One deviation, flagged:** The brief specifies `async for chunk in resp.aiter_raw():` for the generator's iteration method. However, `aiter_raw()` raises `httpx.StreamConsumed` unconditionally when used with `httpx.MockTransport` on this repo's httpx 0.28.1 — reproduced and independently confirmed by the controller. Since the Global Constraints mandate `httpx.MockTransport` exclusively (no real network calls permitted), the two requirements are incompatible in this version.

**Resolution (controller-approved):** Used `aiter_bytes()` instead. This is not a workaround but the correct call for two reasons:
1. Binding constraint: MockTransport is mandatory; `aiter_raw()` cannot function under it
2. Behavioral correctness: The gateway's own endpoint (Task 10) does not forward the upstream's `Content-Encoding` header; downstream clients expect plain `text/event-stream` without compression headers, making already-decoded bytes (via `aiter_bytes()`) semantically correct. Using `aiter_raw()` would only be safe if `Content-Encoding` were forwarded verbatim, which is not part of this plan.

Verified: all 4 streaming tests pass with `aiter_bytes()` and produce byte-identical output for uncompressed test fixtures.

## Constraints Verified

- ✓ No real network calls — httpx.MockTransport used exclusively
- ✓ No files touched besides `openai_adapter.py` and its test file
- ✓ Pure byte passthrough — no transformation logic introduced
- ✓ Unmapped models unchanged — default `api_key_header="Authorization"` preserves wire output byte-for-byte
- ✓ No I/O dependencies in headers generation — `_headers()` is pure function

## Next Steps

Ready for code review and merge to `feature/multi-provider-routing`.
