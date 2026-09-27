"""Public contracts for constrained Oracle PO port resolution."""

from domains.orders.port_resolution.resolver import (
    MAX_DESTINATION_CHARS,
    MAX_REASON_CHARS,
    PROMPT_VERSION,
    resolve_destination,
)
from domains.orders.port_resolution.types import (
    PortCandidate,
    PortResolutionDecision,
    PortResolutionError,
)

__all__ = [
    "MAX_DESTINATION_CHARS",
    "MAX_REASON_CHARS",
    "PROMPT_VERSION",
    "PortCandidate",
    "PortResolutionDecision",
    "PortResolutionError",
    "resolve_destination",
]
