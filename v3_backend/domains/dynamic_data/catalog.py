"""Stable identifiers shared by configurable system-table features."""

from uuid import UUID

PRODUCT_TABLE_ID = UUID("025588dd-ae63-5607-9e78-1179a500ed6e")
BUSINESS_CLASSIFICATION_FIELD_ID = UUID("cc022b56-84dc-5fa1-aa37-caa607ebaa38")
BUSINESS_CLASSIFICATION_OPTIONS = (
    (UUID("30293f3e-d76a-5a50-9292-d0fc8e2f15c9"), "中标产品"),
    (UUID("51493dd7-3b7f-5c60-98f0-c88f03c96080"), "未中标产品"),
    (UUID("9ed864e6-c49a-5eec-adae-5b7b21d1415c"), "临时产品"),
    (UUID("9ba6cb77-7f2f-581a-8f17-a5ca75fe9524"), "未分类"),
)
