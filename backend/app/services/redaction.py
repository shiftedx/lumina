"""Secret and path redaction for logs and admin diagnostics."""
from __future__ import annotations

import logging
import re
import traceback

SECRETS = (
    (re.compile(r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie|x-csrf-token|x-api-key|api-key|x-emby-token|x-mediabrowser-token)(\s*[:=]\s*)[^\r\n]+"), r"\1\2[redacted]"),
    (re.compile(r"(?i)\bbearer\s+[\w.~+/=-]+"), "Bearer [redacted]"),
    (re.compile(r"#(invite|reset)=[^\s&\"'<>]+"), r"#\1=[redacted]"),
    (
        re.compile(
            r"(?i)(?<![A-Za-z0-9-])((?:password|passwd|new_password|current_password|token|access_token|refresh_token|api_key|apikey|"
            r"client_secret|secret|csrf_token|session|sig|lsig|signature)[\"']?\s*[:=]\s*[\"']?)[^\s&\"',;}]+"
        ),
        r"\1[redacted]",
    ),
    (re.compile(r"(/api/webhooks/\d+/)[\w-]+"), r"\1[redacted]"),  # Discord webhook token
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "[redacted]"),
    (re.compile(r"(://)[^/\s:@]+:[^/\s@]+@"), r"\1[redacted]@"),  # URL userinfo
)
# Absolute POSIX paths with at least two segments; API routes stay readable. A
# quoted path (yt-dlp/OSError style) is taken whole, spaces and all.
QUOTED_PATH = re.compile(r"(['\"])/(?!api/)[^'\"\r\n]*/[^'\"\r\n]*\1")
PATH = re.compile(r"(?<![\w/:.~-])/(?!api/)(?:[^\s/'\"<>]+/)+[^\s'\"<>,;)]*")


def redact(text: str, *, paths: bool = False) -> str:
    for pattern, replacement in SECRETS:
        text = pattern.sub(replacement, text)
    return PATH.sub("[path]", QUOTED_PATH.sub(r"\1[path]\1", text)) if paths else text


_base_factory = logging.getLogRecordFactory()


def _redacting_factory(*args, **kwargs) -> logging.LogRecord:  # noqa: ANN002, ANN003
    record = _base_factory(*args, **kwargs)
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 - a malformed record is left for the handler to report
        return record
    redacted = redact(message)
    if redacted != message:
        record.msg, record.args = redacted, None
    if record.exc_info and record.exc_info[0] is not None:
        record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)).rstrip("\n"))
    return record


def install_log_redaction() -> None:
    """Redact every log record at creation, so every handler (ours, uvicorn's, a test's) sees the same safe text."""
    logging.setLogRecordFactory(_redacting_factory)
