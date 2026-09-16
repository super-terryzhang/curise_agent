"""Remove bearer values and credential query strings before application log output."""

import logging
import re

_URL_CREDENTIAL = re.compile(r"((?:https?://[^\s?\"'<>]+|/uploads/[^\s?\"'<>]+))\?[^\s\"'<>]+")
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~-]+", re.IGNORECASE)


class CredentialFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = _URL_CREDENTIAL.sub(r"\1?[redacted]", record.getMessage())
        record.msg = _BEARER.sub("Bearer [redacted]", message)
        record.args = ()
        return True


def install() -> None:
    for name in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(name).addFilter(CredentialFilter())
    for handler in logging.getLogger().handlers:
        handler.addFilter(CredentialFilter())
