"""v3 business tools registered against general-agent's `@tool` decorator.

Each module here contains pure tool definitions — handlers receive
`ctx: ToolContext` and unwrap V3Deps via `get_deps(ctx)`. There is no
closure / per-request registry; the tools register once at import time
and the per-request Agent uses `view(...)` to scope which subset is
enabled.

Toolsets:
- `business` — db query, order reads, inquiry generation
- `notes`   — provided by general-agent (remember/recall) when v3 wires
  V3Memory into the Agent's `memory` slot

Importing this package fires every `@tool` decorator. Call
`enabled_v3_business_toolset_names()` to get the list of names a v3
chat agent should request via `AgentConfig.toolsets`.
"""

from __future__ import annotations

# Side-effect imports — each module's `@tool` decorators register on the
# global REGISTRY at import time.
from agent.runtime.tools import artifacts as _artifacts  # noqa: F401
from agent.runtime.tools import data_upload as _data_upload  # noqa: F401
from agent.runtime.tools import documents as _documents  # noqa: F401
from agent.runtime.tools import inquiry as _inquiry  # noqa: F401
from agent.runtime.tools import masterdata as _masterdata  # noqa: F401
from agent.runtime.tools import orders as _orders  # noqa: F401
from agent.runtime.tools import propose as _propose  # noqa: F401
from agent.runtime.tools import query_db as _query_db  # noqa: F401
from agent.runtime.tools import skills_loader as _skills_loader  # noqa: F401

V3_BUSINESS_TOOLSET = "business"


def enabled_v3_business_tool_names() -> list[str]:
    """Names of every v3 business tool. Use as `AgentConfig.toolsets`
    when you want only v3 tools (no general-agent defaults like web_search).

    `query_db` is in the `business_advanced` toolset and is NOT included
    here — it's only handed to admin users via the factory's role check.
    `get_inquiry_state` was removed in P2 cleanup (use `get_order_detail`).
    """
    return [
        # HITL gateway
        "propose_action",
        # documents
        "list_documents",
        "get_document",
        "search_documents",
        "read_document_section",
        # orders
        "list_orders",
        "get_order_detail",
        "update_order",
        "rematch_order",
        "get_order_financials",
        "what_if_cost_change",
        "analyze_order_financials",
        # masterdata
        "search_masterdata",
        "get_product_price_history",
        "update_product",
        "update_supplier",
        # data upload
        "list_my_uploads",
        "parse_uploaded_file",
        "preview_upload",
        "inspect_upload_row",
        "get_upload_template",
        # artifacts — Generative-UI hand-off (A2UI pattern)
        "present_artifact",
        # inquiry
        "generate_inquiry",
        "regenerate_supplier_inquiry",
        # skill loader (progressive disclosure — Anthropic Skills pattern)
        "load_skill",
    ]
