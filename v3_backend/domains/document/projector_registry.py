"""Projector registry — maps a doc_type to a function that creates the
typed subtype record from a Document (ADR-0001).

Phase 2: registry is empty. `project()` is a no-op — it just logs.

Phase 3 registers `project_purchase_order` under the "purchase_order" key:

    from domains.document.projector_registry import register_projector

    def project_purchase_order(doc, db): ...
    register_projector("purchase_order", project_purchase_order)

The projector is responsible for:
- Reading `doc.extracted_data["markdown"]`
- Creating/updating the subtype row (Order, Invoice, ...)
- Committing the transaction

It is NOT responsible for deciding whether to project (caller decides based
on doc_type) nor for HTTP/Agent-level concerns.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from domains.document.models import Document

logger = logging.getLogger(__name__)

Projector = Callable[[Document, Session], Any]


@dataclass
class _Registry:
    projectors: dict[str, Projector] = field(default_factory=dict)

    def register(self, doc_type: str, fn: Projector) -> None:
        if doc_type in self.projectors:
            logger.warning("projector_registry: overriding existing projector for %s", doc_type)
        self.projectors[doc_type] = fn
        logger.info("projector_registry: registered %s", doc_type)

    def get(self, doc_type: str) -> Projector | None:
        return self.projectors.get(doc_type)

    def clear(self) -> None:
        self.projectors.clear()

    def count(self) -> int:
        return len(self.projectors)


_registry = _Registry()


def register_projector(doc_type: str, fn: Projector) -> None:
    _registry.register(doc_type, fn)


def project(document: Document, db: Session) -> Any | None:
    """Dispatch the document's doc_type to its projector, if any.

    Returns whatever the projector returns, or None if no projector is
    registered for this doc_type (which is the Phase 2 default).
    """
    if document.doc_type is None or document.doc_type == "unknown":
        return None
    fn = _registry.get(document.doc_type)
    if fn is None:
        logger.debug(
            "projector_registry: no projector for doc_type=%s (document_id=%s)",
            document.doc_type,
            document.id,
        )
        return None
    return fn(document, db)


def has_projector(doc_type: str) -> bool:
    return _registry.get(doc_type) is not None


def reset_for_tests() -> None:
    _registry.clear()
