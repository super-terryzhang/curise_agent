"""P4 unit-side: `_build_memory_preamble` cost-ceiling guards.

测试目标：
    确保 memory preamble 的 hard cap（30 条、2000 chars）真的生效。
    这是花钱风险的安全网：用户存 1000 条 memory，每次 turn 也不会塞
    1000 × 100 tokens 进 prompt。

这条单测不调 LLM，纯函数测试 —— 不需要 RUN_AGENT_RELIABILITY=1。
"""

from __future__ import annotations

from agent.runtime.factory import (
    _MEMORY_MAX_CHARS,
    _MEMORY_MAX_ENTRIES,
    _build_memory_preamble,
)
from agent.runtime.memory_adapter import V3Memory


def test_zero_memories_returns_empty_string(db, seed_user):
    """No memories → no preamble. Don't inject an empty section."""
    mem = V3Memory(db, user_id=seed_user.id)
    assert _build_memory_preamble(mem) == ""


def test_single_memory_renders_inline(db, seed_user):
    mem = V3Memory(db, user_id=seed_user.id)
    mem.append("favorite ship is MV Pioneer", tag="user_preference")
    out = _build_memory_preamble(mem)
    assert "MV Pioneer" in out
    assert "<USER_FACTS" in out
    assert "</USER_FACTS>" in out
    # Anti-injection delimiter must say "data, not instructions"
    assert "data" in out and "instructions" in out


def test_entry_count_capped(db, seed_user):
    """Storing 50 entries → preamble renders at most _MEMORY_MAX_ENTRIES."""
    mem = V3Memory(db, user_id=seed_user.id)
    for i in range(50):
        mem.append(f"fact number {i}", tag="user_preference")
    out = _build_memory_preamble(mem)
    rendered_lines = [ln for ln in out.split("\n") if ln.startswith("- [")]
    assert len(rendered_lines) <= _MEMORY_MAX_ENTRIES, (
        f"rendered {len(rendered_lines)} entries, cap is {_MEMORY_MAX_ENTRIES}"
    )


def test_total_chars_capped(db, seed_user):
    """A single huge memory value gets per-entry truncated; many entries
    hit the body cap too. Either way, total ≤ cap + delimiters."""
    mem = V3Memory(db, user_id=seed_user.id)
    long_value = "x" * 5000
    for i in range(5):
        mem.append(long_value, tag="bulk")
    out = _build_memory_preamble(mem)
    # The body is bounded by _MEMORY_MAX_CHARS plus the truncation note.
    # Delimiters (headers/footers) add maybe ~300 chars more — give a
    # generous safety margin in the assertion.
    assert len(out) < _MEMORY_MAX_CHARS + 600, (
        f"preamble exceeded ceiling: {len(out)} > {_MEMORY_MAX_CHARS + 600}"
    )


def test_per_entry_truncation(db, seed_user):
    """A single 5000-char memory must be truncated, not echoed in full."""
    mem = V3Memory(db, user_id=seed_user.id)
    mem.append("x" * 5000, tag="user_preference")
    out = _build_memory_preamble(mem)
    # Per-entry cap is 300; entry would render as
    # "- [user_preference] x" * 297 chars then "..."
    assert "..." in out
    # The full 5000-char run should NOT survive.
    assert ("x" * 1000) not in out


def test_other_users_memories_not_leaked(db, seed_user):
    """Crucial: factory injects memory scoped to user_id. We seed user A's
    memory, then check that V3Memory for user A surfaces it but user
    B (different id) doesn't see anything."""
    from infrastructure.security import hash_password
    from domains.identity.models import User

    user_b = User(
        email="ghost@example.com",
        hashed_password=hash_password("x"),
        role="employee",
        full_name="Ghost",
        is_active=True,
    )
    db.add(user_b)
    db.commit()
    db.refresh(user_b)

    mem_a = V3Memory(db, user_id=seed_user.id)
    mem_a.append("secret of user A", tag="user_preference")

    mem_b = V3Memory(db, user_id=user_b.id)
    out_b = _build_memory_preamble(mem_b)
    assert "secret of user A" not in out_b
    assert out_b == "", "user B should see empty preamble"
