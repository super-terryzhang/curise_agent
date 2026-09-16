"""Order domain errors."""

from __future__ import annotations


class OrderError(Exception):
    """Base for order errors."""


class NotFound(OrderError):
    """404."""


class Conflict(OrderError):
    """409 — state conflict / integrity violation."""


class BadRequest(OrderError):
    """400 — bad input."""


class StatusConflict(OrderError):
    """409 — operation not valid for the order's current status."""
