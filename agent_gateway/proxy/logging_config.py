"""Log redaction for the gateway.

No credential (an Authorization/x-api-key header, a Bearer token, an
api_key value) may reach a log sink. The app does not log request headers
today; this is a defense-in-depth layer so that if it ever does -- or if
uvicorn/root logging emits something credential-bearing -- the secret is
scrubbed before it is written. Install once at app startup.
"""

from __future__ import annotations

import logging
import re

_PATTERNS = [
    re.compile(r'(?i)(authorization"?\s*[:=]\s*"?)(bearer\s+)?[A-Za-z0-9._\-]+'),
    re.compile(r'(?i)(x-api-key"?\s*[:=]\s*"?)[A-Za-z0-9._\-]+'),
    re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._\-]+'),
    re.compile(r'(?i)(api[_-]?key"?\s*[:=]\s*"?)[A-Za-z0-9._\-]+'),
]
_REDACTED = "***REDACTED***"

_SENSITIVE_HEADERS = {"authorization", "x-api-key", "api-key", "api_key", "cookie"}


def _redact(text: str) -> str:
    for pattern in _PATTERNS:
        # Keep the leading label (group 1) if the pattern captured one; the
        # bare "bearer <tok>" pattern has no group, so fall back to the label.
        text = pattern.sub(
            lambda m: (m.group(1) if m.groups() else "bearer ") + _REDACTED, text
        )
    return text


class RedactingFilter(logging.Filter):
    """Rewrites each record's fully-formatted message with credentials masked.
    Always returns True -- it scrubs, it never drops."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _redact(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


def scrub_headers(headers: dict) -> dict:
    """Return a copy of `headers` safe to log: sensitive values are masked."""
    return {
        k: (_REDACTED if str(k).lower() in _SENSITIVE_HEADERS else v)
        for k, v in headers.items()
    }


def install_log_redaction() -> None:
    """Attach the redacting filter to the root and uvicorn loggers."""
    log_filter = RedactingFilter()
    for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactingFilter) for f in logger.filters):
            logger.addFilter(log_filter)
