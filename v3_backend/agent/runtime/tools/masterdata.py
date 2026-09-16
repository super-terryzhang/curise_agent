"""Master-data tools — search/update/create/delete on `products` /
`suppliers` (read on countries / ports / categories).

Tier policy (per ADR-0007 acceptance + 2025-2026 industry consensus):

| Operation                              | Tier | Rationale                            |
| -------------------------------------- | ---- | ------------------------------------ |
| Search any entity                      | 1    | Read                                 |
| Update product name / description      | 1    | Reversible, no financial impact      |
| Update product price / code / unit     | 2    | Affects matching + financials        |
| Update supplier contact / name         | 1    | Reversible                           |
| Create / delete any entity             | 2    | Adds/removes business state          |

The agent never sees `update_product_financial` / `delete_product` etc.
as direct tools — those routes go through `propose_action` with a
specialized action name.
"""

from __future__ import annotations

import json

from general_agent import ToolContext, tool
from sqlalchemy import select

from agent.runtime.approvals import ActionSpec, register_action
from agent.runtime.deps import get_deps
from domains.masterdata import service as md_service
from domains.masterdata.errors import MasterdataError
from domains.masterdata.models import Category, Country, Port, Product, Supplier
from domains.masterdata.schemas import ProductUpdate, SupplierUpdate

_KNOWN_ENTITIES = ("products", "suppliers", "countries", "ports", "categories")
_MAX_LIMIT = 50

# Fields the agent may update directly on `products`. Everything else
# (price, code, supplier_id, country_id, ...) must go through propose.
_PRODUCT_DIRECT_FIELDS = frozenset(
    {"product_name_en", "product_name_jp", "country_of_origin", "brand", "status"}
)
# Fields the agent may update directly on `suppliers`.
_SUPPLIER_DIRECT_FIELDS = frozenset(
    {"name", "contact", "email", "phone", "address", "zip_code", "fax", "status"}
)


# ─── search_masterdata ──────────────────────────────────────


@tool(toolset="business", emoji="🔍")
def search_masterdata(
    entity: str, query: str = "", limit: int = 20, *, ctx: ToolContext
) -> str:
    """Search master-data: products / suppliers / countries / ports / categories.

    Use this for THREE shapes of question:

    1. LOOKUP — "find product XYZ", "what suppliers ship from Yokohama",
       "list categories". Pass `query` + a reasonable `limit`.
    2. COUNT — "how many products" / "总共多少 X". Pass `limit=1` and read
       `total_matching` from the response.
    3. AGGREGATE over a small result set — "single most expensive fruit",
       "cheapest supplier", "products under 500円". Workflow:
         a. Call with `limit=1` to learn `total_matching`.
         b. If `total_matching ≤ 50`, call again with `limit=total_matching`
            to get every row in `items`. Each item is a FULL record (not a
            preview) and includes id, name, code, unit, price, supplier_id,
            country_id for products. Compute max/min/sort yourself.
         c. If `total_matching > 50`, narrow with a sharper `query` first.

    Read-only. For documents use list_documents/search_documents; for
    orders use list_orders.

    Args:
        entity: One of products / suppliers / countries / ports / categories.
        query: Substring match (case-insensitive). Products/suppliers also
            search by code. Empty = first `limit` rows.
        limit: Max rows in `items` (1-50, default 20). Does NOT cap the
            `total_matching` count.

    Returns a JSON object with these fields:
        - total_matching: int — exact COUNT(*) of rows matching the query.
          THIS is the answer to "how many" questions, NOT len(items).
        - returned: int — number of items actually included (≤ limit).
        - truncated: bool — True if total_matching > returned.
        - items: list — the actual rows (up to `limit`). For products, each
          row carries id, product_name_en, product_name_jp, code, unit,
          price, supplier_id, country_id — enough to answer price/sort
          questions client-side without another tool.
    """
    # ARCH NOTE: this tool is a thin wrapper around `domains.masterdata.service`
    # by design. Earlier versions wrote SQL directly here, which (a) duplicated
    # query logic the service layer already had, and (b) introduced a
    # misnamed `total = len(items)` field that made the agent report wrong
    # counts. See ADR-0002. Do NOT re-introduce direct DB access here —
    # extend the service instead.
    deps = get_deps(ctx)
    if entity not in _KNOWN_ENTITIES:
        return f"Error: unknown entity '{entity}'. Valid: {', '.join(_KNOWN_ENTITIES)}."
    bounded = max(1, min(int(limit) if limit else 20, _MAX_LIMIT))
    q = (query or "").strip() or None

    try:
        if entity == "products":
            result = md_service.list_products(deps.db, search=q, limit=bounded)
        elif entity == "suppliers":
            result = md_service.search_suppliers(deps.db, search=q, limit=bounded)
        elif entity == "countries":
            result = md_service.search_countries(deps.db, search=q, limit=bounded)
        elif entity == "ports":
            result = md_service.search_ports(deps.db, search=q, limit=bounded)
        else:  # categories
            result = md_service.search_categories(deps.db, search=q, limit=bounded)
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"

    # Service returns rich rows with name lookups (country_name, supplier_name,
    # etc.) — useful on the web UI, but they bloat agent output past
    # general_agent's 16000-char tool-output cap. We project to a SLIM shape
    # that's enough for the agent to identify items + ask follow-up questions.
    # If the LLM needs full detail (e.g. for update_product), it should call
    # the targeted lookup tool instead.
    _SLIM_FIELDS_BY_ENTITY = {
        "products": ("id", "product_name_en", "product_name_jp", "code",
                     "unit", "price", "supplier_id", "country_id"),
        "suppliers": ("id", "name", "country_id", "country_name", "contact"),
        "countries": ("id", "name", "code"),
        "ports": ("id", "name", "code", "country_id"),
        "categories": ("id", "name", "code"),
    }
    raw_items = result.get("items") or []
    slim_fields = _SLIM_FIELDS_BY_ENTITY[entity]
    items = [{k: row.get(k) for k in slim_fields if k in row} for row in raw_items]

    total_matching = int(result.get("total", 0))

    # Field semantics (DO NOT change without updating tool description):
    #   total_matching — DB count of rows matching the query (the answer
    #                    to "how many X").
    #   returned       — len(items) — how many rows we actually included
    #                    in this response after applying `limit`.
    #   truncated      — True if total_matching > returned.
    return json.dumps(
        {
            "entity": entity,
            "total_matching": total_matching,
            "returned": len(items),
            "truncated": total_matching > len(items),
            "items": items,
        },
        ensure_ascii=False,
        default=str,
    )


# ─── update_product / update_supplier ────────────────────────


@tool(toolset="business", emoji="✏️")
def update_product(product_id: int, fields: str, *, ctx: ToolContext) -> str:
    """Update a product's safe metadata fields (name, brand, country_of_origin, status).

    For price / code / supplier_id / unit / pack_size and other
    matching-affecting fields, use `propose_action` with
    action="update_product_financial".

    Args:
        product_id: Numeric product id.
        fields: JSON object with field-name → new-value pairs.
    """
    deps = get_deps(ctx)
    try:
        body_dict = json.loads(fields) if fields else {}
        if not isinstance(body_dict, dict):
            raise ValueError("must be a JSON object")
    except (ValueError, json.JSONDecodeError) as exc:
        return f"Error: invalid `fields` JSON — {exc}"
    if not body_dict:
        return "Error: no fields supplied"

    unknown = [k for k in body_dict if k not in _PRODUCT_DIRECT_FIELDS]
    if unknown:
        return (
            f"Error: fields {unknown} require approval — use propose_action with "
            "action='update_product_financial'."
        )
    try:
        result = md_service.update_product(
            deps.db, product_id, ProductUpdate(**body_dict), actor_id=deps.user_id, source="ai"
        )
    except MasterdataError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"
    return json.dumps(result, ensure_ascii=False, default=str)


@tool(toolset="business", emoji="✏️")
def update_supplier(supplier_id: int, fields: str, *, ctx: ToolContext) -> str:
    """Update a supplier's safe metadata fields (name, contact, email, phone, address).

    For category-association changes, use `propose_action` with
    action="update_supplier_categories".

    Args:
        supplier_id: Numeric supplier id.
        fields: JSON object with field-name → new-value pairs.
    """
    deps = get_deps(ctx)
    try:
        body_dict = json.loads(fields) if fields else {}
        if not isinstance(body_dict, dict):
            raise ValueError("must be a JSON object")
    except (ValueError, json.JSONDecodeError) as exc:
        return f"Error: invalid `fields` JSON — {exc}"
    if not body_dict:
        return "Error: no fields supplied"

    unknown = [k for k in body_dict if k not in _SUPPLIER_DIRECT_FIELDS]
    if unknown:
        return (
            f"Error: fields {unknown} require approval — use propose_action."
        )
    try:
        result = md_service.update_supplier(
            deps.db, supplier_id, SupplierUpdate(**body_dict)
        )
    except MasterdataError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"
    return json.dumps(result, ensure_ascii=False, default=str)


@tool(toolset="business", emoji="📋")
def get_product_price_history(product_id: int, limit: int = 10, offset: int = 0,
                              max_version: int | None = None, legacy: bool = False,
                              *, ctx: ToolContext) -> str:
    """Read one product's price ledger. Initial/baseline are not price changes.

    Pass the returned max_version on later pages. legacy=True returns incomplete
    old field records separately; never infer unknown prices/currency from them.
    This tool only reads. Restoring a price is a separate confirmed operation.
    """
    deps = get_deps(ctx)
    from domains.masterdata import price_history
    try:
        result = price_history.list_history(deps.db, product_id,
            limit=max(1, min(limit, 20)), offset=max(0, offset),
            max_version=max_version, legacy=legacy)
    except MasterdataError as exc:
        return f"Error: {exc}"
    return json.dumps(result, ensure_ascii=False, default=str)


# ─── Tier-2 dispatch handlers ────────────────────────────────


def _dispatch_update_product_financial(deps, target_id, payload):  # noqa: ANN001
    body = ProductUpdate(**payload)
    if body.expected_revision is None:
        raise ValueError("旧审批缺少产品版本，请重新发起改价审批")
    return md_service.update_product(deps.db, target_id, body, actor_id=deps.user_id, source="ai")


def _dispatch_create_product(deps, target_id, payload):  # noqa: ANN001 ARG001
    from domains.masterdata.schemas import ProductCreate

    return md_service.create_product(deps.db, ProductCreate(**payload), actor_id=deps.user_id, source="ai")


def _dispatch_create_supplier(deps, target_id, payload):  # noqa: ANN001 ARG001
    from domains.masterdata.schemas import SupplierCreate

    return md_service.create_supplier(deps.db, SupplierCreate(**payload))


def _dispatch_update_supplier_categories(deps, target_id, payload):  # noqa: ANN001
    body = SupplierUpdate(**payload)
    return md_service.update_supplier(deps.db, target_id, body)


def _dispatch_delete_masterdata(deps, target_id, payload):  # noqa: ANN001
    entity = (payload or {}).get("entity")
    if entity == "products":
        if (payload or {}).get("expected_revision") is None:
            raise ValueError("旧审批缺少产品版本，请重新发起删除审批")
        md_service.delete_product(deps.db, target_id, actor_id=deps.user_id, source="ai", expected_revision=payload["expected_revision"])
    elif entity == "suppliers":
        md_service.delete_supplier(deps.db, target_id)
    elif entity == "countries":
        md_service.delete_country(deps.db, target_id)
    elif entity == "ports":
        md_service.delete_port(deps.db, target_id)
    elif entity == "categories":
        md_service.delete_category(deps.db, target_id)
    else:
        raise ValueError(f"unknown entity '{entity}'")
    return {"deleted_entity": entity, "deleted_id": target_id}


register_action(
    ActionSpec(
        name="update_product_financial",
        target_kind="product",
        description="Update price / code / unit / supplier on a product.",
        dispatch=_dispatch_update_product_financial,
    )
)
register_action(
    ActionSpec(
        name="create_product",
        target_kind="product",
        description="Create a new product master-data row.",
        dispatch=_dispatch_create_product,
    )
)
register_action(
    ActionSpec(
        name="create_supplier",
        target_kind="supplier",
        description="Create a new supplier.",
        dispatch=_dispatch_create_supplier,
    )
)
register_action(
    ActionSpec(
        name="update_supplier_categories",
        target_kind="supplier",
        description="Reassign a supplier's categories.",
        dispatch=_dispatch_update_supplier_categories,
    )
)
register_action(
    ActionSpec(
        name="delete_masterdata",
        target_kind="masterdata",
        description="Delete a master-data row (product / supplier / country / port / category).",
        dispatch=_dispatch_delete_masterdata,
    )
)
