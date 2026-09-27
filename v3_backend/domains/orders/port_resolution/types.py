"""Typed contracts for constrained Oracle destination resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class PortCandidate:
    id: int
    name: str
    country_id: int


@dataclass(frozen=True, slots=True)
class PortResolutionDecision:
    status: Literal["matched", "unmatched"]
    port_id: int | None
    reason: str
    model: str
    prompt_version: str
    candidate_snapshot_hash: str


class PortResolutionError(RuntimeError):
    """A provider or contract failure that must become manual review."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
