"""Domain error hierarchy for master data.

Service functions raise these; the HTTP layer maps them to status codes.
Never raise `HTTPException` inside service code.
"""

from __future__ import annotations


class MasterdataError(Exception):
    """Base for master-data errors."""


class NotFound(MasterdataError):
    """404 — entity does not exist."""


class Conflict(MasterdataError):
    """409 — unique violation or reference-still-exists."""


class BadRequest(MasterdataError):
    """400 — bad input (invalid date, unknown FK, etc.)."""


class UpstreamUnavailable(MasterdataError):
    """502 — external API failed."""
