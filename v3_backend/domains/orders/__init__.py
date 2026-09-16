"""Orders domain — `purchase_order` subtype of Document.

Importing this package registers the PO classifier rule. After successful
enrichment, the document workflow calls the late-bound public automation hook
to create/reuse an Order and continue through matching, grouping, inquiry and
anomaly detection; the legacy explicit create endpoint remains compatible.

The `enrich_purchase_order_document` function below runs LLM extraction
during the document workflow without touching the Order model; projection is
the next independently recorded stage.
"""

from domains.orders import classifier_rules, projection, schemas, service  # noqa: F401
from domains.orders import oracle_models as _oracle_models  # noqa: F401
from domains.orders.enrichment import enrich_purchase_order_document
from domains.orders.models import Order


def automatic_from_document(document_id: int):
    """Late-bound public hook avoids orders↔inquiry import cycles at startup."""
    from domains.orders.automation import automatic_from_document as run

    return run(document_id)


def automatic_order_pipeline(order_id: int, *, trace=None):
    """Late-bound public hook for automatic stages 5–8."""
    from domains.orders.automation import automatic_order_pipeline as run

    return run(order_id, trace=trace)

__all__ = [
    "service",
    "schemas",
    "Order",
    "automatic_from_document",
    "automatic_order_pipeline",
    "enrich_purchase_order_document",
]
