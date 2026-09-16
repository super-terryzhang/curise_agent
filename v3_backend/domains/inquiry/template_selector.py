"""Template selection — pure functions for binding a SupplierTemplate to a supplier.

Mirrors v2's `resolve_template` / `select_template`. Production-only filter:
templates without a root-level `zone_config` are considered legacy and are
excluded — only zoned templates are chosen automatically.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from domains.inquiry.models import SupplierTemplate
from domains.inquiry.schemas import TemplateBinding

ResolveResult = tuple[SupplierTemplate | None, str, list[dict[str, Any]]]


def template_has_zone_config(template: SupplierTemplate | None) -> bool:
    if template is None:
        return False
    styles = template.template_styles
    return isinstance(styles, dict) and isinstance(styles.get("zones"), dict)


def _filter_production(
    templates: Sequence[SupplierTemplate],
) -> list[SupplierTemplate]:
    return [t for t in templates if template_has_zone_config(t)]


def resolve_template(supplier_id: int, all_templates: Sequence[SupplierTemplate]) -> ResolveResult:
    """Pick a template for a supplier.

    Returns (template, method, candidates):
    - exact `supplier_ids`/`supplier_id` match → (template, "exact", [])
    - no exact → (None, "candidates", [{id, name, country_id}, ...])
    - no production templates at all → (None, "unavailable", [])
    """
    pool = _filter_production(all_templates)
    if not pool:
        return None, "unavailable", []

    for t in pool:
        if t.supplier_ids and supplier_id in t.supplier_ids:
            return t, "exact", []
    for t in pool:
        if t.supplier_id == supplier_id:
            return t, "exact", []

    candidates = [{"id": t.id, "name": t.template_name, "country_id": t.country_id} for t in pool]
    return None, "candidates", candidates


def select_template(
    supplier_id: int,
    all_templates: Sequence[SupplierTemplate],
    *,
    template_id_override: int | None = None,
) -> ResolveResult:
    """Like `resolve_template` but auto-picks the first candidate when no exact match.

    Honors a user-supplied `template_id_override` if it points to a production
    template; raises `ValueError` if not.
    """
    pool = _filter_production(all_templates)
    if template_id_override is not None:
        override = next((t for t in all_templates if t.id == template_id_override), None)
        if override is None:
            raise ValueError(f"模板 {template_id_override} 不存在")
        if not template_has_zone_config(override):
            raise ValueError(
                f"模板 {template_id_override} 已下架：当前只允许使用带 zone_config 的模板"
            )
        return override, "user_selected", []

    template, method, candidates = resolve_template(supplier_id, pool)
    if template is not None:
        return template, method, candidates
    if candidates:
        first = next((t for t in pool if t.id == candidates[0]["id"]), None)
        if first is not None:
            return first, "candidate_auto", candidates
    return None, "unavailable", []


def to_binding(template: SupplierTemplate | None, method: str) -> TemplateBinding:
    if template is None:
        return TemplateBinding(method=method)
    return TemplateBinding(id=template.id, name=template.template_name, method=method)


__all__ = [
    "resolve_template",
    "select_template",
    "template_has_zone_config",
    "to_binding",
]
