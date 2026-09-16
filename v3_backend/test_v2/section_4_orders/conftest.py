"""Section 4 — Orders: ensure the PO detector is registered.

Section 3 has an autouse fixture that resets the document classifier
registry between tests. If Section 3 runs before Section 4, the
purchase_order detector — registered as an import side effect of
`domains.orders.classifier_rules` — is gone.

This fixture re-registers it before every Section 4 test so the suite
order doesn't matter.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _ensure_po_detector_registered():
    """Re-register `purchase_order` detector before every test in this section."""
    from domains.document import classifier
    from domains.orders.classifier_rules import detect_purchase_order

    # Snapshot whatever was registered (so we don't double-register)
    existing = {(name, fn) for name, fn in classifier._registry.rules}  # type: ignore[attr-defined]
    if ("purchase_order", detect_purchase_order) not in existing:
        classifier.register_rule("purchase_order", detect_purchase_order)
    yield
