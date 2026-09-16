"""Section 1 — LINE webhook event-id dedup: `domains.line.service.record_event_id`.

测试目标：
    1. 第一次见到一个 event_id 必须返回 True 并写入 v3_line_event_log；
    2. 同一 event_id 再调用 → False（基于 UNIQUE 约束违反检测）；
    3. 多个不同 event_id 各自返回 True；
    4. 空 event_id（防御性兜底）→ True，但不写表（防 PK 冲突）；
    5. 重复调用 5+ 次同一 id 始终返回 False（False 是稳定的，不会突然 True）；
    6. dedup 行真的持久化（其他 session 也能看到）。

为什么重要：
    LINE 在 5xx / timeout 时会重投同一 webhook。
    没有 dedup → 同一条消息会被 agent 处理两次：
    可能扣两次配额、发两条回复、生成两条 audit log。
    一次重投 = 一次幂等性 bug。

设计方法：
    每条 path 一个 test；session_factory 创建一个第二 session 来验证持久化跨 session
    可见（in-memory SQLite + StaticPool 共享一条连接，所以 commit 后另一个 session 立刻看得到）。
"""

from __future__ import annotations

from domains.line import service as line_service
from domains.line.models import LineEventLog


# ─── First sighting ──────────────────────────────────────────


def test_first_event_id_returns_true(db) -> None:
    """Inaugural event_id is novel → True so caller proceeds with processing."""
    assert line_service.record_event_id(db, event_id="evt-001") is True


def test_first_event_id_persists_row(db) -> None:
    """The row must land in the table with received_at populated."""
    line_service.record_event_id(db, event_id="evt-001")
    rows = db.query(LineEventLog).all()
    assert len(rows) == 1
    assert rows[0].event_id == "evt-001"
    assert rows[0].received_at is not None


# ─── Duplicate sighting ──────────────────────────────────────


def test_duplicate_event_id_returns_false(db) -> None:
    """Second call with the same id → False (UNIQUE-violation path)."""
    line_service.record_event_id(db, event_id="evt-001")
    assert line_service.record_event_id(db, event_id="evt-001") is False


def test_duplicate_event_id_does_not_create_second_row(db) -> None:
    """Duplicate must not double-insert — IntegrityError rolls back."""
    line_service.record_event_id(db, event_id="evt-001")
    line_service.record_event_id(db, event_id="evt-001")
    rows = db.query(LineEventLog).all()
    assert len(rows) == 1


def test_repeated_dup_stays_false_5_times(db) -> None:
    """Repeated calls always return False — never flips back to True."""
    line_service.record_event_id(db, event_id="evt-001")
    for _ in range(5):
        assert line_service.record_event_id(db, event_id="evt-001") is False


# ─── Distinct events ─────────────────────────────────────────


def test_distinct_event_ids_each_return_true(db) -> None:
    """Independent IDs are independent — no cross-dedup."""
    assert line_service.record_event_id(db, event_id="evt-001") is True
    assert line_service.record_event_id(db, event_id="evt-002") is True
    assert line_service.record_event_id(db, event_id="evt-003") is True


def test_distinct_event_ids_all_persisted(db) -> None:
    """Each independent event must land in the table."""
    line_service.record_event_id(db, event_id="evt-001")
    line_service.record_event_id(db, event_id="evt-002")
    line_service.record_event_id(db, event_id="evt-003")
    ids = {r.event_id for r in db.query(LineEventLog).all()}
    assert ids == {"evt-001", "evt-002", "evt-003"}


# ─── Defensive: empty event id ───────────────────────────────


def test_empty_event_id_returns_true_without_writing(db) -> None:
    """Empty event_id is defensive fall-through: LINE always sends one,
    so this only triggers on a malformed request. Return True so the
    downstream agent still runs (and downstream guards catch dup-fire),
    but DO NOT write a row with empty PK."""
    assert line_service.record_event_id(db, event_id="") is True
    assert db.query(LineEventLog).count() == 0


def test_empty_event_id_does_not_collide_with_real_one(db) -> None:
    """Two empty event_ids in a row must both return True (no fake dedup),
    because we never wrote a row for the first one."""
    assert line_service.record_event_id(db, event_id="") is True
    assert line_service.record_event_id(db, event_id="") is True


# ─── Cross-session persistence ───────────────────────────────


def test_dedup_visible_across_sessions(session_factory) -> None:
    """A commit in session A must be visible to a fresh session B —
    otherwise concurrent workers could each treat the same event as new."""
    s_a = session_factory()
    s_b = session_factory()
    try:
        first = line_service.record_event_id(s_a, event_id="evt-cross")
        second = line_service.record_event_id(s_b, event_id="evt-cross")
        assert first is True
        assert second is False
    finally:
        s_a.close()
        s_b.close()
