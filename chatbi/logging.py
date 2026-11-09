"""Structured JSON logging with request correlation and secret redaction."""

from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Iterator
from uuid import uuid4


request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|authorization|password|passwd|pwd)(\s*[=:]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(mysql(?:\+\w+)?://[^:\s]+:)([^@\s]+)(@)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def redact(value: object) -> str:
    text = str(value)
    text = _SECRET_PATTERNS[0].sub(r"\1\2***", text)
    text = _SECRET_PATTERNS[1].sub(r"\1***\3", text)
    return _SECRET_PATTERNS[2].sub("***", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "message": redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def new_request_id() -> str:
    return uuid4().hex


@contextmanager
def request_context(request_id: str | None = None) -> Iterator[str]:
    resolved = request_id or new_request_id()
    token: Token[str] = request_id_var.set(resolved)
    try:
        yield resolved
    finally:
        request_id_var.reset(token)
