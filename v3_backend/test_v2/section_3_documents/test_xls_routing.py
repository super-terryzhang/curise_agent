"""Section 3 — Documents: legacy .xls routing must go to markitdown, not openpyxl.

测试目标：
    `application/vnd.ms-excel`（legacy CFBF 二进制 Excel，常见于皇家邮轮订单）
    必须由 markitdown_extractor 处理，不能命中 excel_extractor。后者用 openpyxl
    只支持 OOXML .xlsx，遇到二进制 .xls 会抛 "no valid workbook part"。

为什么重要：
    皇家邮轮（CCL）的订单是真实的 legacy .xls（"Composite Document File V2"，
    code page 932）。2026-06-06 Felix 上传后系统失败 = 业务卡死。
    R1 2026-06-16 修复：从 excel_extractor._SUPPORTED 移除 ms-excel mime，
    让 router 第一个匹配命中 markitdown_extractor（它用 xlrd 支持 .xls）。

设计方法：
    - 走 production 路由（不直接调 markitdown），证明 router 第一个 supports()
      命中的就是 markitdown
    - 不打 Gemini，纯本地 markitdown 解析
"""

from __future__ import annotations


def test_excel_extractor_does_not_claim_legacy_xls_mime():
    """excel_extractor 必须不再接 application/vnd.ms-excel —— 这是把
    皇家订单卡死的根因。"""
    from domains.document.extraction import excel_extractor

    assert "application/vnd.ms-excel" not in excel_extractor._SUPPORTED


def test_markitdown_extractor_claims_legacy_xls_mime():
    """markitdown_extractor 必须接 application/vnd.ms-excel。"""
    from domains.document.extraction import markitdown_extractor

    assert "application/vnd.ms-excel" in markitdown_extractor._SUPPORTED_MIMES


def test_router_dispatches_legacy_xls_to_markitdown():
    """router 第一个命中的 extractor 处理 application/vnd.ms-excel 必须
    是 markitdown —— 校验路由顺序和 supports() 结果合起来的行为。"""
    from domains.document.extraction.router import _bootstrap, _EXTRACTORS

    _bootstrap()
    mime = "application/vnd.ms-excel"
    first_match = next((e for e in _EXTRACTORS if e.supports(mime)), None)
    assert first_match is not None
    assert first_match.name == "markitdown", (
        f"legacy .xls must route to markitdown, got {first_match.name!r}; "
        "if you renamed the extractor or changed router order, update this test "
        "after verifying real .xls files still extract."
    )
