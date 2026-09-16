"""Section 4 — Orders: loading_date plumbing across extraction → DB → API.

测试目标：
    新加的 `Order.loading_date` 字段（2026-06-16 Felix R6）必须完整贯通三层：
    1. Gemini extraction schema 包含 loading_date（否则 LLM 永远不输出）
    2. projection.`_PO_FIELD_KEYS` 包含 loading_date（否则 metadata→Order 漏字段）
    3. service.`_UPDATABLE_COLUMNS` 包含 loading_date（否则 PATCH 改不了）

为什么重要：
    一个字段从 LLM JSON 到 Order ORM 到 HTTP 响应有三道独立关卡。任何一道
    漏配，前端就显示空白且看不出原因（不是错而是寂静）。三段都断言能
    挡住 "schema 加了但 projection 没改" 这种半成品。

设计方法：
    全部纯 Python 断言，不打开 DB / 不发 HTTP，几毫秒跑完。覆盖契约本身，
    不重测 Gemini 的提取质量（那是另一个层级的 eval）。
"""

from __future__ import annotations


# ─── 1. Extraction schema contract ────────────────────────────


def test_extraction_response_schema_includes_loading_date():
    """LLM 字段提取 schema 必须申明 loading_date —— 否则 Gemini 严格
    typed JSON 拒绝输出该字段。"""
    from domains.orders._llm_extractor import _RESPONSE_SCHEMA

    metadata_props = _RESPONSE_SCHEMA["properties"]["order_metadata"]["properties"]
    assert "loading_date" in metadata_props
    assert metadata_props["loading_date"]["nullable"] is True


def test_base_prompt_documents_loading_date():
    """Prompt 必须显式提到 loading_date，否则即使 schema 允许 LLM 也不会主动找。
    业务上还要让模型知道 loading_date ≠ delivery_date。"""
    from domains.orders._llm_extractor import _BASE_PROMPT

    assert "loading_date" in _BASE_PROMPT
    # Make sure we tell the LLM why it's distinct from delivery_date,
    # so it doesn't just copy delivery_date into the new field.
    assert "Distinct from delivery_date" in _BASE_PROMPT


# ─── 2. Projection contract (metadata → Order) ────────────────


def test_projection_po_field_keys_includes_loading_date():
    """Extraction 跑完后 _apply_fields 用 _PO_FIELD_KEYS 把 metadata 字段
    写到 Order ORM。漏了这个键，loading_date 即使 LLM 抽到了也落不了库。"""
    from domains.orders.projection import _PO_FIELD_KEYS

    assert "loading_date" in _PO_FIELD_KEYS


# ─── 3. Update API contract ───────────────────────────────────


def test_updatable_columns_includes_loading_date():
    """PATCH /orders/{id} 通过 _UPDATABLE_COLUMNS 决定哪些字段允许改。漏
    了的话，Felix 手工修正 loading_date 的请求会被静默丢弃（200 但没生效）。"""
    from domains.orders.service import _UPDATABLE_COLUMNS

    assert "loading_date" in _UPDATABLE_COLUMNS


def test_order_update_request_schema_includes_loading_date():
    """OrderUpdateRequest Pydantic 模型必须显式列出 loading_date。否则
    Pydantic exclude_unset 模式下 PATCH 请求里这个字段会被丢弃，
    根本到不了 service 层。"""
    from domains.orders.schemas import OrderUpdateRequest

    fields = OrderUpdateRequest.model_fields
    assert "loading_date" in fields


# ─── 4. Response schema contracts ─────────────────────────────


def test_order_list_item_schema_includes_loading_date():
    """前端列表页要显示 loading_date 列。schema 必须把它列为 top-level
    字段，前端才能稳定读 row.loading_date 而不是 row.order_metadata.loading_date
    （后者契约更脆，容易因为 metadata key drift 失败）。"""
    from domains.orders.schemas import OrderListItem

    assert "loading_date" in OrderListItem.model_fields


def test_order_detail_schema_includes_loading_date():
    """详情页同理，需要 top-level loading_date 用于显示和编辑。"""
    from domains.orders.schemas import OrderDetail

    assert "loading_date" in OrderDetail.model_fields
