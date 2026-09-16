"""Register the purchase_order detector with `domains.document.classifier`.

Importing this module has the side effect of adding the detector to the
document classifier's rule chain. `domains.orders.__init__` imports it so
that Order domain startup auto-registers.
"""

from __future__ import annotations

import logging

from domains.document import ExtractedDocument, classifier

logger = logging.getLogger(__name__)

# Keyword buckets — each group adds to the score when any member appears
_PRIMARY_KEYWORDS = (
    "purchase order",
    "po number",
    "po no",
    "order no",
    "order number",
    "発注書",
    "発注番号",
    "注文書",
    "採購",
)
_SECONDARY_KEYWORDS = (
    "vendor",
    "supplier",
    "deliver",
    "ship name",
    "vessel",
    "invoice",
    "total amount",
    "quantity",
    "unit price",
)


def _extract_all_text(doc: ExtractedDocument) -> str:
    """Concatenate title + markdown for case-insensitive keyword scanning."""
    parts: list[str] = []
    title = doc.get("title")
    if title:
        parts.append(str(title))
    md = doc.get("markdown")
    if md:
        parts.append(str(md))
    return "\n".join(parts).lower()


def detect_purchase_order(doc: ExtractedDocument) -> str | None:
    """Return "purchase_order" if the document looks like a PO, else None."""
    text = _extract_all_text(doc)
    if not text:
        return None

    primary_hits = sum(1 for kw in _PRIMARY_KEYWORDS if kw in text)
    secondary_hits = sum(1 for kw in _SECONDARY_KEYWORDS if kw in text)

    # Decision rule: either one strong primary hit, or two secondary hits
    # plus one primary. Tunable; kept deliberately simple for Phase 3.
    if primary_hits >= 1:
        return "purchase_order"
    if secondary_hits >= 3:
        logger.debug("PO detected via %d secondary keywords", secondary_hits)
        return "purchase_order"
    return None


# Register on import
classifier.register_rule("purchase_order", detect_purchase_order)
