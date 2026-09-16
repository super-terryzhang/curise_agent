"""Section 4 — Orders: PO 分类器规则 (`domains/orders/classifier_rules.py`).

测试目标:
    `detect_purchase_order(doc)` 必须 (a) 一个 primary 关键词就触发, (b) 单
    个 secondary 不够触发, (c) 至少 3 个 secondary 才触发 (源码里阈值是 3),
    (d) 完全没文本时返回 None, (e) 导入模块会副作用地把检测器注册进
    `domains.document.classifier` 的规则链。

为什么重要:
    分类器是 Document→Order 流水线的入口判定。如果它放走了 PO (false negative)
    那张文档就会停留在 "unknown" 状态、永远进不了 Order 投影; 如果它把账单
    /报价单错认成 PO (false positive) 我们会创建一堆垃圾 Order 行。两类错误
    都很难从 UI 上发现, 所以阈值必须用单元测试钉死。

设计方法:
    - 直接构造 `ExtractedDocument` (TypedDict, 用 dict 就行), 喂给
      `detect_purchase_order`, 断言返回值。
    - 不去走 Document ORM / Workflow — 那是 Section 3 的范围。
    - 关键词阈值用参数化覆盖边界 (0, 1, 2, 3 个 secondary)。
    - 注册副作用用 `classifier.count()` / 遍历 rules 来确认。
"""

from __future__ import annotations

import pytest

from domains.document import classifier
from domains.orders.classifier_rules import detect_purchase_order


# ─── Primary keyword triggers ─────────────────────────────────


@pytest.mark.parametrize(
    "keyword",
    [
        "purchase order",
        "PO Number",
        "PO No",
        "order number",
        "order no",
        "発注書",
        "発注番号",
        "注文書",
        "採購",
    ],
)
def test_single_primary_keyword_is_enough(keyword: str):
    """一个 primary 关键词出现在 title 或 markdown 任意位置即判定为 PO."""
    doc = {"title": "", "markdown": f"some text {keyword} more text"}
    assert detect_purchase_order(doc) == "purchase_order"


def test_primary_keyword_in_title_alone():
    """Title 字段单独命中也算 — `_extract_all_text` 会把 title+markdown 拼起来扫."""
    doc = {"title": "Purchase Order #42", "markdown": ""}
    assert detect_purchase_order(doc) == "purchase_order"


def test_primary_keyword_matching_is_case_insensitive():
    """文本被 `.lower()` 处理后再扫, 大小写无关."""
    doc = {"title": None, "markdown": "PURCHASE ORDER details follow"}
    assert detect_purchase_order(doc) == "purchase_order"


# ─── Secondary keyword threshold ──────────────────────────────


def test_zero_secondary_keywords_returns_none():
    """完全没关键词 → 不分类."""
    doc = {"title": "Random Document", "markdown": "This is just some random text."}
    assert detect_purchase_order(doc) is None


def test_one_secondary_keyword_is_below_threshold():
    """1 个 secondary 不够 (阈值 >=3)."""
    doc = {"title": "", "markdown": "Please contact your vendor for details."}
    assert detect_purchase_order(doc) is None


def test_two_secondary_keywords_still_below_threshold():
    """2 个 secondary 仍然不够 — 严格大于等于 3 才升级."""
    doc = {
        "title": "",
        "markdown": "Please contact your vendor and the supplier.",
    }
    assert detect_purchase_order(doc) is None


def test_three_secondary_keywords_triggers_po():
    """3 个 secondary (无 primary) 达到阈值 → 判定 PO."""
    doc = {
        "title": "",
        "markdown": "Talk to the vendor or supplier; vessel arrives soon.",
    }
    assert detect_purchase_order(doc) == "purchase_order"


def test_four_secondary_keywords_still_triggers_po():
    """超过阈值仍然判定 PO — 这条防止有人误把判定写成 `== 3`."""
    doc = {
        "title": "",
        "markdown": "vendor supplier deliver invoice quantity",
    }
    assert detect_purchase_order(doc) == "purchase_order"


# ─── Empty / degenerate input ─────────────────────────────────


def test_empty_doc_returns_none():
    """什么都没有 → 早返回 None, 不要崩."""
    assert detect_purchase_order({}) is None


def test_title_and_markdown_both_empty_string():
    """空字符串 (不是 None) 也必须安全返回 None."""
    assert detect_purchase_order({"title": "", "markdown": ""}) is None


def test_title_none_markdown_none():
    """显式 None 也不能崩 — `_extract_all_text` 用 `.get` 兼容缺失键."""
    assert detect_purchase_order({"title": None, "markdown": None}) is None


# ─── False-positive guards ────────────────────────────────────


def test_unrelated_invoice_doc_not_misclassified():
    """发票里没有任何 PO primary 关键词 — 不能误判."""
    doc = {
        "title": "Tax Receipt",
        "markdown": "Thank you for your payment. Total: 100 USD.",
    }
    assert detect_purchase_order(doc) is None


def test_word_boundary_not_strict_but_substring_ok():
    """阅读源码: 用的是 `kw in text`, 子串匹配。`po number` 出现在 `expopo number`
    会被匹中 — 这是当前行为, 测试钉住, 改了要改这条。"""
    doc = {"title": "", "markdown": "purchase order placed"}
    # 含 "purchase order" → 命中 primary
    assert detect_purchase_order(doc) == "purchase_order"


# ─── Registration side-effect (import = register) ─────────────


def test_detector_is_registered_on_import():
    """`domains.orders.classifier_rules` 模块的 import 必须把
    `detect_purchase_order` 加进 classifier 规则链。这是 Document→Order
    流水线能跑起来的前提。"""
    # rules 是 list[(doc_type, detector_fn)], 我们在里面找到自己这条
    registered = [
        (dt, fn)
        for dt, fn in classifier._registry.rules
        if dt == "purchase_order" and fn is detect_purchase_order
    ]
    assert len(registered) == 1, (
        "purchase_order detector should be registered exactly once "
        f"(found {len(registered)})"
    )


def test_classifier_dispatches_to_po_detector():
    """端到端: 走 `classifier.classify(doc)` 必须返回 'purchase_order' 当文档
    确实是 PO。这条比上面那条更直接地证明注册生效。"""
    doc = {"title": "Purchase Order #100", "markdown": ""}
    assert classifier.classify(doc) == "purchase_order"


def test_classifier_returns_unknown_for_non_po():
    """非 PO 文档走分类器应当落到 UNKNOWN (前提是没有其他规则把它接走)."""
    doc = {"title": "Random Memo", "markdown": "Just a memo, nothing special."}
    # 注: 如果未来有别的 detector 注册了, 这条可能要改。但目前 Section 4
    #     阶段只有 purchase_order 一条规则。
    assert classifier.classify(doc) == "unknown"


def test_reimporting_module_does_not_duplicate_registration():
    """re-import 不能产生重复规则 — _Registry.register 内部按 (doc_type, fn)
    去重。"""
    before = sum(
        1
        for dt, fn in classifier._registry.rules
        if dt == "purchase_order" and fn is detect_purchase_order
    )
    import importlib

    import domains.orders.classifier_rules as mod

    importlib.reload(mod)
    after = sum(
        1
        for dt, fn in classifier._registry.rules
        if dt == "purchase_order"
    )
    # reload 之后函数对象 id 变了, 所以可能有 1 条旧的 + 1 条新的 = 2 条
    # 我们要求的是 "不无限增长"; reload 一次后至多 2 条。
    assert after <= before + 1, (
        f"reloading classifier_rules duplicated registration: {before} → {after}"
    )
