"""Inquiry domain —询价 (Phase 4 SupplierTemplate CRUD; Phase 5 orchestration).

Public API:
- `service`: SupplierTemplate CRUD (Phase 4) + inquiry state read (Phase 5)
- `orchestrator`: full + per-supplier inquiry execution + cancel (Phase 5)
- `schemas`: Pydantic DTOs incl. `InquiryState.to_legacy_dict()`
- `sinks`: progress sink Protocol + concrete sinks (NullSink, AsyncQueueSink, ...)
- `SupplierTemplate` / `Inquiry` / `InquirySupplier`: ORM (re-exported for Alembic + repository helpers)
"""

from domains.inquiry import orchestrator, schemas, service, sinks
from domains.inquiry.models import Inquiry, InquirySupplier, SupplierTemplate
from domains.inquiry.template_selector import select_template


def queue_inquiry_for_group(db, group_id: int):
    """Public domain entry for creating the next arrangement inquiry version."""
    return orchestrator.queue_inquiry_for_group(db, group_id)


def run_inquiry_for_group(group_id: int, **kwargs):
    """Public domain entry for executing an arrangement inquiry version."""
    return orchestrator.run_inquiry_for_group(group_id, **kwargs)


def resolve_supplier_template(supplier_id: int, templates):
    """Public read-only entry for the template choice used by generation."""
    return select_template(supplier_id, templates)

__all__ = [
    "service",
    "orchestrator",
    "schemas",
    "sinks",
    "SupplierTemplate",
    "Inquiry",
    "InquirySupplier",
    "queue_inquiry_for_group",
    "run_inquiry_for_group",
    "resolve_supplier_template",
]
