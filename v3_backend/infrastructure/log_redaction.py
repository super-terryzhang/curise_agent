"""Remove bearer values and credential query strings before application log output."""

import logging
import re

_URL_CREDENTIAL = re.compile(r"((?:https?://[^\s?\"'<>]+|/uploads/[^\s?\"'<>]+))\?[^\s\"'<>]+")
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~-]+", re.IGNORECASE)


def _redact(value: str) -> str:
    value = _URL_CREDENTIAL.sub(r"\1?[redacted]", value)
    return _BEARER.sub("Bearer [redacted]", value)


class CredentialFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple):
            record.msg = _redact(str(record.msg))
            record.args = tuple(
                _redact(value) if isinstance(value, str) else value
                for value in record.args
            )
            return True

        record.msg = _redact(record.getMessage())
        record.args = ()
        return True


def install() -> None:
    for name in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(name).addFilter(CredentialFilter())
    for handler in logging.getLogger().handlers:
        handler.addFilter(CredentialFilter())
