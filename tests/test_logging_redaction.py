"""Credential scrubbing for logs (Item 5c)."""

from __future__ import annotations

import logging

from agent_gateway.proxy.logging_config import (
    RedactingFilter,
    install_log_redaction,
    scrub_headers,
)


def test_scrub_headers_masks_credentials():
    out = scrub_headers({
        "Authorization": "Bearer sk-abc123",
        "x-api-key": "k-xyz",
        "Accept": "application/json",
    })
    assert out["Authorization"] == "***REDACTED***"
    assert out["x-api-key"] == "***REDACTED***"
    assert out["Accept"] == "application/json"


def test_filter_redacts_bearer_token_in_message(caplog):
    logger = logging.getLogger("test.redact")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="test.redact"):
        logger.info("calling upstream with Authorization: Bearer sk-supersecret-123")
    assert "sk-supersecret-123" not in caplog.text
    assert "***REDACTED***" in caplog.text


def test_filter_redacts_x_api_key_in_message(caplog):
    logger = logging.getLogger("test.redact2")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="test.redact2"):
        logger.info('headers={"x-api-key": "k-topsecret-999"}')
    assert "k-topsecret-999" not in caplog.text
    assert "***REDACTED***" in caplog.text


def test_install_is_idempotent():
    install_log_redaction()
    install_log_redaction()
    root = logging.getLogger("")
    assert sum(isinstance(f, RedactingFilter) for f in root.filters) == 1
