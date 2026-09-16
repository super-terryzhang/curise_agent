"""Section 3 — Documents: service.user_tags add/remove/list/filter.

测试目标：
    `Document.user_tags` 是用户手动打的标签（区别于系统/LLM 写的 `tags`）。
    service 层负责归一化、去重、上限、过滤组合的所有规则。

为什么重要：
    - 标签是用户最容易看到的字段（filter sidebar / chip 上）。坏一次显示
      全员都看见。
    - 归一化漂移 → "Celebrity Cruise" / "celebrity_cruise" / "celebrity-cruise"
      被存成三份 → list_user_tags 重复出现。
    - 容量 (30 上限) / 大小写不敏感的 remove / 多 tag AND 语义都有过
      过 bug。

设计方法：
    - 归一化是纯函数 → 大量 parametrize 覆盖。
    - add/remove/list 走真实 DB session（用 conftest 的 `db` + `seed_user`
      fixture）。系统/用户 tag 都是 JSON 列，断言 doc.user_tags 直接读。
"""

from __future__ import annotations

import pytest

from domains.document import service
from domains.document.models import Document
from domains.document.service import BadRequest


# ─── _normalize_user_tag: happy normalization ─


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Celebrity Cruise", "celebrity-cruise"),
        ("celebrity_cruise", "celebrity-cruise"),
        ("CELEBRITY-CRUISE", "celebrity-cruise"),
        ("  Beef  Supplier  ", "beef-supplier"),  # collapsed whitespace runs
        ("a/b", "a-b"),  # slash → hyphen
        ("ALPHA", "alpha"),  # plain casing
        ("urgent", "urgent"),  # already canonical
    ],
)
def test_normalize_converts_to_lowercase_kebab(raw, expected):
    """LLM-generated tags use kebab-case. User tags must match so the filter
    sidebar shows one chip per concept."""
    assert service._normalize_user_tag(raw) == expected


def test_normalize_preserves_unicode_alphanumerics():
    """Chinese / Japanese chars are alphanumeric per Python's `str.isalnum`,
    so they survive normalization unchanged (we don't romanize)."""
    assert service._normalize_user_tag("紧急") == "紧急"
    assert service._normalize_user_tag("Test 紧急") == "test-紧急"


def test_normalize_drops_emoji_and_random_punctuation():
    """Emojis and punctuation that isn't `- .` get silently dropped."""
    # Only alphanumeric + - . survive; ! and * get dropped.
    out = service._normalize_user_tag("urgent!*")
    assert out == "urgent"


# ─── _normalize_user_tag: rejection ─


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_normalize_rejects_empty_input(raw):
    """An empty/whitespace-only tag is a user error — must raise BadRequest,
    not silently store as `""`."""
    with pytest.raises(BadRequest):
        service._normalize_user_tag(raw)


def test_normalize_rejects_punctuation_only():
    """`@@!!` has zero alphanumerics — after dropping all punctuation we'd
    save an empty string. Reject explicitly so user sees what's wrong."""
    with pytest.raises(BadRequest, match="字母或数字"):
        service._normalize_user_tag("@@!!")


@pytest.mark.parametrize(
    "raw",
    [
        "file_type:fake",
        "doc_type:invoice",
        "key:value",
        "a:b:c",
    ],
)
def test_normalize_rejects_colons_to_avoid_clashing_with_system_tags(raw):
    """`:` prefix is reserved for system tags like `file_type:pdf`. A user
    typing one must see a clear error, not silently get `file-type-fake`."""
    with pytest.raises(BadRequest, match=":"):
        service._normalize_user_tag(raw)


def test_normalize_rejects_too_long_tag():
    with pytest.raises(BadRequest, match="长度"):
        service._normalize_user_tag("x" * 100)


# ─── DB fixture for add/remove/list tests ─


@pytest.fixture
def doc_for_user(db, seed_user):
    """One Document owned by seed_user, status=extracted, no tags yet."""
    doc = Document(
        user_id=seed_user.id,
        filename="report.pdf",
        file_type="pdf",
        file_size_bytes=100,
        doc_type="unknown",
        status="extracted",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


# ─── add_user_tag ─


def test_add_user_tag_appends_normalized_value(db, seed_user, doc_for_user):
    """The raw input is normalized to kebab-case before being stored."""
    service.add_user_tag(
        db,
        document_id=doc_for_user.id,
        user_id=seed_user.id,
        is_admin=False,
        tag="Celebrity Cruise",
    )
    db.refresh(doc_for_user)
    assert doc_for_user.user_tags == ["celebrity-cruise"]


def test_add_user_tag_is_idempotent_for_duplicate_input(db, seed_user, doc_for_user):
    """Adding the same tag twice (or a case-variant) leaves exactly one
    copy — used to dedupe double-click submissions."""
    service.add_user_tag(
        db, document_id=doc_for_user.id, user_id=seed_user.id, is_admin=False, tag="urgent"
    )
    service.add_user_tag(
        db, document_id=doc_for_user.id, user_id=seed_user.id, is_admin=False, tag="URGENT"
    )
    db.refresh(doc_for_user)
    assert doc_for_user.user_tags == ["urgent"]


def test_add_user_tag_caps_at_max_per_doc(db, seed_user, doc_for_user):
    """A single document gets at most _MAX_TAGS_PER_DOC user tags.
    Overflow raises BadRequest with a clear message."""
    for i in range(service._MAX_TAGS_PER_DOC):
        service.add_user_tag(
            db,
            document_id=doc_for_user.id,
            user_id=seed_user.id,
            is_admin=False,
            tag=f"tag-{i}",
        )
    with pytest.raises(BadRequest, match="最多"):
        service.add_user_tag(
            db,
            document_id=doc_for_user.id,
            user_id=seed_user.id,
            is_admin=False,
            tag="overflow-tag",
        )


# ─── remove_user_tag ─


def test_remove_user_tag_drops_existing(db, seed_user, doc_for_user):
    service.add_user_tag(
        db, document_id=doc_for_user.id, user_id=seed_user.id, is_admin=False, tag="todrop"
    )
    service.remove_user_tag(
        db, document_id=doc_for_user.id, user_id=seed_user.id, is_admin=False, tag="todrop"
    )
    db.refresh(doc_for_user)
    assert doc_for_user.user_tags is None


def test_remove_user_tag_is_idempotent_when_tag_missing(db, seed_user, doc_for_user):
    """Removing a non-existent tag returns the doc as-is (no 404). Network
    retries / double-clicks must NOT error."""
    # Remove on a doc with no tags at all
    response = service.remove_user_tag(
        db, document_id=doc_for_user.id, user_id=seed_user.id, is_admin=False, tag="missing"
    )
    assert response.id == doc_for_user.id  # served successfully


def test_remove_user_tag_rejects_empty_string(db, seed_user, doc_for_user):
    with pytest.raises(BadRequest):
        service.remove_user_tag(
            db, document_id=doc_for_user.id, user_id=seed_user.id, is_admin=False, tag=""
        )


# ─── list_user_tags ─


def test_list_user_tags_returns_name_count_sorted(db, seed_user):
    """Sidebar uses [{name, count}] sorted by name. `count` = number of
    docs carrying the tag."""
    for tags in (["alpha", "beta"], ["beta", "gamma"], ["alpha"]):
        d = Document(
            user_id=seed_user.id,
            filename="x.pdf",
            file_type="pdf",
            doc_type="unknown",
            status="extracted",
            user_tags=tags,
        )
        db.add(d)
    db.commit()

    result = service.list_user_tags(db, user_id=seed_user.id, is_admin=False)
    assert result == [
        {"name": "alpha", "count": 2},
        {"name": "beta", "count": 2},
        {"name": "gamma", "count": 1},
    ]


def test_list_user_tags_returns_empty_when_no_user_tags(db, seed_user):
    """Empty DB → empty list (not None, not error)."""
    assert service.list_user_tags(db, user_id=seed_user.id, is_admin=False) == []


# ─── list_documents filtering ─


def test_list_documents_filters_by_single_user_tag(db, seed_user):
    a = Document(
        user_id=seed_user.id,
        filename="a.pdf",
        file_type="pdf",
        doc_type="unknown",
        status="extracted",
        user_tags=["celebrity-cruise"],
    )
    b = Document(
        user_id=seed_user.id,
        filename="b.pdf",
        file_type="pdf",
        doc_type="unknown",
        status="extracted",
        user_tags=["other"],
    )
    db.add_all([a, b])
    db.commit()

    page = service.list_documents(
        db, user_id=seed_user.id, is_admin=False, tags=["celebrity-cruise"]
    )
    assert page.total == 1
    assert page.items[0].id == a.id


def test_list_documents_tag_filter_also_matches_system_tags(db, seed_user):
    """Filter is the union of system `tags` + `user_tags`. LLM-written
    `beef-supplier` should be found via the same filter widget."""
    a = Document(
        user_id=seed_user.id,
        filename="a.pdf",
        file_type="pdf",
        doc_type="unknown",
        status="extracted",
        tags=["beef-supplier"],
    )
    b = Document(
        user_id=seed_user.id,
        filename="b.pdf",
        file_type="pdf",
        doc_type="unknown",
        status="extracted",
        tags=["other-supplier"],
    )
    db.add_all([a, b])
    db.commit()

    page = service.list_documents(
        db, user_id=seed_user.id, is_admin=False, tags=["beef-supplier"]
    )
    assert page.total == 1
    assert page.items[0].id == a.id


def test_list_documents_multi_tag_filter_uses_AND_semantics(db, seed_user):
    """Two tags = doc must carry BOTH (intersection). OR would surface
    everything in either bucket, defeating the purpose of multi-select."""
    a = Document(
        user_id=seed_user.id,
        filename="a.pdf",
        file_type="pdf",
        doc_type="unknown",
        status="extracted",
        user_tags=["urgent", "celebrity-cruise"],
    )
    b = Document(
        user_id=seed_user.id,
        filename="b.pdf",
        file_type="pdf",
        doc_type="unknown",
        status="extracted",
        user_tags=["urgent"],
    )
    c = Document(
        user_id=seed_user.id,
        filename="c.pdf",
        file_type="pdf",
        doc_type="unknown",
        status="extracted",
        user_tags=["celebrity-cruise"],
    )
    db.add_all([a, b, c])
    db.commit()

    page = service.list_documents(
        db, user_id=seed_user.id, is_admin=False, tags=["urgent", "celebrity-cruise"]
    )
    assert page.total == 1
    assert page.items[0].id == a.id


def test_list_documents_filter_ignores_blank_tag_input(db, seed_user):
    """`tags=["", "  "]` is equivalent to no filter — don't accidentally
    AND against an empty string."""
    a = Document(
        user_id=seed_user.id,
        filename="a.pdf",
        file_type="pdf",
        doc_type="unknown",
        status="extracted",
        user_tags=["urgent"],
    )
    db.add(a)
    db.commit()

    page = service.list_documents(
        db, user_id=seed_user.id, is_admin=False, tags=["", "  "]
    )
    assert page.total == 1  # filter degenerates to "no filter"
