"""Section 7 — Agent memory: cross-session typed memory CRUD.

测试目标：
    `agent/memory/store.py` 的 MemoryStore — 这是 chat agent 的"长期记忆"
    存储面 (v3_agent_memories 表)。它必须:
      - upsert 是真 upsert: 同一 (user_id, memory_type, key) 第二次写不复制
      - get 拿最新 value
      - list 只返回本 user_id 的, 别人的看不到 (user_scoped)
      - touch 增 access_count
      - delete 把行清掉
      - 跨用户 touch 是 no-op (静默忽略, 不抛错, 不改别人的行)

为什么重要：
    Memory 是 agent 永远活着的"侧线状态" — 一旦泄漏:
      * 用户 A 让 agent 记的供应商邮箱被用户 B 看到 → 数据外泄
      * upsert 真的复制 → 反复 chat 后内存爆炸式增长
      * touch 跨用户能改别人的 row → 一个 cheap audit trail 篡改向量

设计方法：
    每个 MemoryStore 在构造时绑死 user_id, 测试用两个 store (user A / user B)
    模拟不同会话。所有断言走 DB 重读或 store API, 不信任 _to_entry 返回。
"""

from __future__ import annotations

from agent.memory import MemoryStore
from agent.storage.models import AgentMemory


# ─── upsert: insert + update in place ────────────────────────


def test_upsert_inserts_new_row_when_key_missing(db) -> None:
    """第一次 upsert 同一 (type, key) → INSERT。"""
    store = MemoryStore(db, user_id=1)
    entry = store.upsert(memory_type="fact", key="favorite_color", value="blue")
    assert entry.id is not None
    rows = db.query(AgentMemory).filter(AgentMemory.user_id == 1).all()
    assert len(rows) == 1
    assert rows[0].value == "blue"


def test_upsert_updates_existing_row_does_not_duplicate(db) -> None:
    """同一 user/type/key 第二次 upsert → UPDATE, 不能产生第二行。"""
    store = MemoryStore(db, user_id=1)
    first = store.upsert(memory_type="fact", key="favorite_color", value="blue")
    second = store.upsert(memory_type="fact", key="favorite_color", value="green")
    assert first.id == second.id
    assert second.value == "green"
    rows = db.query(AgentMemory).filter(AgentMemory.user_id == 1).all()
    assert len(rows) == 1, "不能有第二行"


def test_upsert_does_not_collide_across_different_types(db) -> None:
    """不同 memory_type 但同 key → 两行 (key 只在 type 内唯一)。"""
    store = MemoryStore(db, user_id=1)
    a = store.upsert(memory_type="fact", key="color", value="blue")
    b = store.upsert(memory_type="user_preference", key="color", value="green")
    assert a.id != b.id


# ─── get ──────────────────────────────────────────────────────


def test_get_returns_latest_value_after_upsert(db) -> None:
    """get 不能返回 None / 旧 value — upsert 完立刻能读到新值。"""
    store = MemoryStore(db, user_id=1)
    store.upsert(memory_type="fact", key="color", value="blue")
    store.upsert(memory_type="fact", key="color", value="green")
    e = store.get(memory_type="fact", key="color")
    assert e is not None
    assert e.value == "green"


def test_get_missing_returns_none(db) -> None:
    store = MemoryStore(db, user_id=1)
    assert store.get(memory_type="fact", key="nope") is None


# ─── list: user-scoped ────────────────────────────────────────


def test_list_for_user_returns_only_own_memories(db) -> None:
    """user A 的 store 看不到 user B 的记忆 — 否则 SaaS 漏底。"""
    store_a = MemoryStore(db, user_id=1)
    store_b = MemoryStore(db, user_id=2)
    store_a.upsert(memory_type="fact", key="x", value="alice's")
    store_b.upsert(memory_type="fact", key="x", value="bob's")
    store_b.upsert(memory_type="fact", key="y", value="bob's-too")

    rows_a = store_a.list()
    rows_b = store_b.list()
    assert len(rows_a) == 1
    assert rows_a[0].value == "alice's"
    assert len(rows_b) == 2
    assert {r.value for r in rows_b} == {"bob's", "bob's-too"}


def test_list_filters_by_memory_type(db) -> None:
    """memory_type='fact' 只返回 fact 类型的 row。"""
    store = MemoryStore(db, user_id=1)
    store.upsert(memory_type="fact", key="a", value="A")
    store.upsert(memory_type="user_preference", key="b", value="B")
    only_facts = store.list(memory_type="fact")
    assert len(only_facts) == 1
    assert only_facts[0].memory_type == "fact"


# ─── touch ────────────────────────────────────────────────────


def test_touch_increments_access_count(db) -> None:
    """每次 touch → access_count +1, 用于"最常用的 memory 排前面"的排序。"""
    store = MemoryStore(db, user_id=1)
    e = store.upsert(memory_type="fact", key="k", value="v")
    assert e.access_count == 0
    store.touch(e.id)
    store.touch(e.id)
    refreshed = store.get(memory_type="fact", key="k")
    assert refreshed is not None
    assert refreshed.access_count == 2
    assert refreshed.last_accessed_at is not None


def test_touch_other_users_memory_is_noop(db) -> None:
    """user B 拿到 user A 的 entry id 调 touch → 必须无副作用, 不抛错, 不改 row.
    这是 user-scoped 写入路径的最后一道防线。"""
    store_a = MemoryStore(db, user_id=1)
    store_b = MemoryStore(db, user_id=2)
    e = store_a.upsert(memory_type="fact", key="k", value="v")
    assert e.access_count == 0

    # user B 看到了某个 id, 试着 touch — 应该静默忽略
    store_b.touch(e.id)

    # row 没被改
    row = db.get(AgentMemory, e.id)
    assert row.user_id == 1
    assert row.access_count == 0
    assert row.last_accessed_at is None


# ─── delete ───────────────────────────────────────────────────


def test_delete_removes_row(db) -> None:
    store = MemoryStore(db, user_id=1)
    e = store.upsert(memory_type="fact", key="k", value="v")
    assert store.delete(memory_type="fact", key="k") is True
    assert db.get(AgentMemory, e.id) is None
    # 二次删返回 False
    assert store.delete(memory_type="fact", key="k") is False


def test_delete_other_users_key_does_not_affect_owner(db) -> None:
    """user B 用相同 (type, key) 调 delete — 因为 store 限定 user_id=2,
    user A 的同名 key 不应被删 (各自命名空间独立)。"""
    store_a = MemoryStore(db, user_id=1)
    store_b = MemoryStore(db, user_id=2)
    a = store_a.upsert(memory_type="fact", key="shared_key", value="alice's")

    # user B 没有这个 key, delete 应返回 False
    assert store_b.delete(memory_type="fact", key="shared_key") is False
    # alice 的 row 还在
    assert db.get(AgentMemory, a.id) is not None


# ─── User isolation (sanity check) ────────────────────────────


def test_user_isolation_get_does_not_leak(db) -> None:
    """user A 写, user B get — 必须 None, 即使 type+key 相同。"""
    MemoryStore(db, user_id=1).upsert(
        memory_type="fact", key="favorite", value="alice-secret"
    )
    other = MemoryStore(db, user_id=2).get(memory_type="fact", key="favorite")
    assert other is None
