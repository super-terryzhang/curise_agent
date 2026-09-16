"""Document classifier registry (ADR-0001).

Produces a `doc_type` string from an `ExtractedDocument`. Each document
subtype (Order, Invoice, Quote, ...) registers its own detector function.
The classifier itself is domain-agnostic: it dispatches to the first
detector that returns a non-None verdict.

Phase 2: the registry is empty. `classify()` returns `"unknown"` for
everything. Phase 3 adds the purchase_order detector by calling
`register_rule("purchase_order", detector_fn)` from
`domains/orders/classifier_rules.py`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from domains.document.extraction.schema import ExtractedDocument

logger = logging.getLogger(__name__)

UNKNOWN: str = "unknown"

# A detector returns the doc_type string if it recognizes the document,
# or None to pass (let another detector try).
Detector = Callable[[ExtractedDocument], str | None]


@dataclass
class _Registry:
    rules: list[tuple[str, Detector]] = field(default_factory=list)

    def register(self, doc_type: str, fn: Detector) -> None:
        # Dedupe by (doc_type, fn identity) — re-importing a module that
        # calls register_rule must not create duplicate detectors.
        for existing_type, existing_fn in self.rules:
            if existing_type == doc_type and existing_fn is fn:
                return
        self.rules.append((doc_type, fn))
        logger.info("classifier: registered detector for %s", doc_type)

    def classify(self, doc: ExtractedDocument) -> str:
        for doc_type, fn in self.rules:
            try:
                verdict = fn(doc)
            except Exception:
                logger.exception("classifier: detector for %s raised", doc_type)
                continue
            if verdict:
                return verdict
        return UNKNOWN

    def clear(self) -> None:
        self.rules.clear()

    def count(self) -> int:
        return len(self.rules)


_registry = _Registry()


def register_rule(doc_type: str, fn: Detector) -> None:
    """Public entry point for sub-domains to register their detector."""
    _registry.register(doc_type, fn)


def classify(doc: ExtractedDocument) -> str:
    """Run all registered detectors; return the first non-None verdict or `UNKNOWN`."""
    return _registry.classify(doc)


def reset_for_tests() -> None:
    """Wipe the registry — ONLY for tests that need isolated state."""
    _registry.clear()
