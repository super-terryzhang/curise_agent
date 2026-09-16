"""Section 3 — Documents: classifier + projector dispatch.

测试目标：
    `classifier` 是 doc_type 决策的注册中心；`projector_registry` 是
    doc_type → 落库 projector 函数的注册中心。两个都是 first-rule-wins
    的小型 dispatcher。

为什么重要：
    - 这两个 registry 是 ADR-0001 的核心扩展点。新文档子类型（quote /
      invoice）注册自己的 detector + projector 就接入系统。
    - 一个 detector 抛异常不能让分类雪崩（classifier 必须 catch + 继续）。
    - 注册去重要在（重复 import 不能产生重复 detector）。
    - projector 对 doc_type="unknown" / None 必须 no-op，否则未识别的
      文档会被错的 projector 修改。

设计方法：
    每个 test 都先 `reset_for_tests()` 清空 registry，注册一两个 fake
    detector / projector，断言行为。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from domains.document import classifier, projector_registry
from domains.document.classifier import UNKNOWN, classify, register_rule
from domains.document.extraction.schema import ExtractedDocument
from domains.document.projector_registry import (
    has_projector,
    project,
    register_projector,
)


@pytest.fixture(autouse=True)
def _reset_registries():
    """Each test starts with empty registries — prevents cross-test pollution
    when another module registers a real detector (e.g. orders.classifier_rules)."""
    classifier.reset_for_tests()
    projector_registry.reset_for_tests()
    yield
    classifier.reset_for_tests()
    projector_registry.reset_for_tests()


# ─── classifier: empty registry ─


def test_empty_registry_classifies_everything_as_unknown():
    """No detectors registered → every doc gets UNKNOWN. This is the
    documented Phase-2 baseline."""
    doc: ExtractedDocument = {"markdown": "anything"}
    assert classify(doc) == UNKNOWN
    assert UNKNOWN == "unknown"  # contract


# ─── classifier: registration + dispatch ─


def test_register_rule_dispatches_to_matching_detector():
    """A detector returning a doc_type → classify returns that doc_type."""

    def detector(_doc: ExtractedDocument) -> str | None:
        return "purchase_order"

    register_rule("purchase_order", detector)
    assert classify({"markdown": "PO 12345"}) == "purchase_order"


def test_first_matching_rule_wins():
    """If multiple detectors match, the one registered first takes
    precedence (insertion order)."""
    register_rule("first", lambda d: "first")
    register_rule("second", lambda d: "second")
    assert classify({"markdown": "x"}) == "first"


def test_detector_returning_none_passes_to_next_rule():
    """A `None` verdict means "I don't know" — the chain continues."""
    register_rule("a", lambda d: None)
    register_rule("b", lambda d: "b-matched")
    assert classify({"markdown": "x"}) == "b-matched"


def test_detector_exception_is_swallowed_and_chain_continues():
    """One detector exploding must NOT prevent later detectors from running.
    Otherwise a buggy plugin would make EVERY doc misclassified."""

    def boom(_doc: ExtractedDocument) -> str | None:
        raise RuntimeError("classifier crashed")

    register_rule("crashing", boom)
    register_rule("good", lambda d: "good")

    assert classify({"markdown": "x"}) == "good"


def test_no_detector_matches_returns_unknown():
    register_rule("a", lambda d: None)
    register_rule("b", lambda d: None)
    assert classify({"markdown": "x"}) == UNKNOWN


def test_register_rule_dedupes_by_function_identity():
    """Re-importing a detector module must not create duplicate rules —
    otherwise the same detector fires N times on every classify."""
    fn = lambda d: "X"  # noqa: E731
    register_rule("dup", fn)
    register_rule("dup", fn)  # second call should be no-op
    register_rule("dup", fn)  # third too

    assert classifier._registry.count() == 1


# ─── projector_registry: empty registry ─


def test_project_returns_none_when_no_projector_registered():
    """Phase-2 baseline: with no projectors, project() must safely no-op
    (not raise) — workflow uses None as "nothing to do"."""
    doc = MagicMock()
    doc.doc_type = "purchase_order"
    doc.id = 42
    db = MagicMock()
    assert project(doc, db) is None


def test_project_skips_for_doc_type_unknown():
    """`unknown` is the documented "we don't know what this is" sentinel.
    No projector should ever run for it, even if one is registered."""
    register_projector("unknown", lambda d, s: "wrongly_called")
    doc = MagicMock()
    doc.doc_type = "unknown"
    doc.id = 1
    assert project(doc, MagicMock()) is None


def test_project_skips_for_doc_type_none():
    """None doc_type → still in the upload pipeline → must not project."""
    register_projector("purchase_order", lambda d, s: "wrongly_called")
    doc = MagicMock()
    doc.doc_type = None
    doc.id = 1
    assert project(doc, MagicMock()) is None


# ─── projector_registry: dispatch ─


def test_project_dispatches_to_registered_projector():
    """A registered projector for a doc_type fires with (doc, db) and its
    return value is bubbled back to the caller."""
    captured: dict[str, object] = {}

    def my_proj(document, session):
        captured["doc_id"] = document.id
        captured["session"] = session
        return {"order_id": 99}

    register_projector("purchase_order", my_proj)

    doc = MagicMock()
    doc.doc_type = "purchase_order"
    doc.id = 7
    db = MagicMock()

    result = project(doc, db)
    assert result == {"order_id": 99}
    assert captured["doc_id"] == 7
    assert captured["session"] is db


def test_has_projector_reports_registration():
    assert has_projector("purchase_order") is False
    register_projector("purchase_order", lambda d, s: None)
    assert has_projector("purchase_order") is True
    assert has_projector("invoice") is False


def test_re_registering_same_doc_type_overrides_previous():
    """If two modules register a projector for the same doc_type, the
    second one wins. The registry's job is logging a warning, not
    refusing the override (which would brick deployment ordering)."""
    register_projector("purchase_order", lambda d, s: "first")
    register_projector("purchase_order", lambda d, s: "second")

    doc = MagicMock()
    doc.doc_type = "purchase_order"
    doc.id = 1

    assert project(doc, MagicMock()) == "second"
