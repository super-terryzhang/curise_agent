"""Section 2 — Architecture: scripts/check_arch.py 的 5 条规则.

测试目标：
    守门脚本必须 (a) 抓到真违规、(b) 不冤枉合法代码、(c) 尊重白名单。

为什么重要：
    K1/K2/K3 那类 bug（agent 工具自己写 SQL 绕开 service）的根本预防机制。
    守门挂了我们永远不会及时发现"代码逻辑没问题但架构在烂"这种慢性病。

设计方法：
    每个测试搭一个 fake repo（临时目录），写一个会/不会违反规则的 .py 文件，
    monkeypatch check_arch.REPO_ROOT 指向临时目录，调对应规则函数，
    断言 violations 列表的长度 + 内容。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK_ARCH_PATH = REPO_ROOT / "scripts" / "check_arch.py"


@pytest.fixture
def check_arch_module():
    """Load scripts/check_arch.py once as a module (scripts/ has no __init__.py)."""
    spec = importlib.util.spec_from_file_location("check_arch", CHECK_ARCH_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_arch"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeRepo:
    """Wraps a tmp_path with a .write() helper for adding source files."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p


@pytest.fixture
def fake_repo(tmp_path, check_arch_module, monkeypatch) -> _FakeRepo:
    """Empty fake repo. Tests add files via fake_repo.write(rel, content).
    Each test gets a fresh tmp_path so previous test files don't leak in."""
    monkeypatch.setattr(check_arch_module, "REPO_ROOT", tmp_path)
    return _FakeRepo(tmp_path)


# ─── RULE 1: domains/** + shared/** must NOT import agent.* ────


def test_rule1_catches_domain_importing_agent(fake_repo, check_arch_module):
    fake_repo.write("domains/orders/service.py", "from agent.runtime import deps\n")
    violations = check_arch_module.check_rule_1()
    assert len(violations) == 1
    assert "RULE-1" in violations[0].rule
    assert "domains/orders/service.py" in str(violations[0].path)


def test_rule1_catches_shared_importing_agent(fake_repo, check_arch_module):
    fake_repo.write("shared/util.py", "import agent.memory\n")
    violations = check_arch_module.check_rule_1()
    assert len(violations) == 1
    assert "shared/util.py" in str(violations[0].path)


def test_rule1_does_not_fire_on_agent_importing_domain(fake_repo, check_arch_module):
    """The forbidden direction is business → agent. The reverse is the
    normal architecture; rule 1 must stay silent."""
    fake_repo.write("agent/runtime/tools/orders.py", "from domains.orders import service\n")
    assert check_arch_module.check_rule_1() == []


def test_rule1_does_not_match_similar_named_imports(fake_repo, check_arch_module):
    """`agent_logger` and `agentic_x` are NOT `agent.*` — must not false-positive."""
    fake_repo.write(
        "domains/orders/service.py",
        "import agent_logger\nfrom agentic_helpers import x\n",
    )
    assert check_arch_module.check_rule_1() == []


# ─── RULE 2: agent/runtime/tools/** must NOT touch db directly ──


@pytest.mark.parametrize("op", ["commit", "add", "query", "delete", "flush", "execute"])
def test_rule2_catches_every_banned_db_method(fake_repo, check_arch_module, op):
    """All 6 banned db methods caught. db.execute was added 2026-05-11 after
    K1/K2/K3 incident (agent tools writing raw SQL bypassed services)."""
    fake_repo.write(
        "agent/runtime/tools/orders.py",
        f"def f(db):\n    db.{op}(thing)\n",
    )
    violations = check_arch_module.check_rule_2()
    assert len(violations) == 1
    assert "RULE-2" in violations[0].rule


def test_rule2_whitelists_query_db_py(fake_repo, check_arch_module):
    """query_db.py's whole purpose is to let the agent execute raw SQL."""
    fake_repo.write("agent/runtime/tools/query_db.py", "def f(db):\n    db.execute(sql)\n")
    assert check_arch_module.check_rule_2() == []


def test_rule2_whitelists_propose_py(fake_repo, check_arch_module):
    """propose.py writes to v3_pending_actions — agent-internal HITL state,
    has no service layer, db writes are correct."""
    fake_repo.write("agent/runtime/tools/propose.py", "def f(db):\n    db.commit()\n")
    assert check_arch_module.check_rule_2() == []


def test_rule2_ignores_storage_and_memory_paths(fake_repo, check_arch_module):
    """agent/storage/ and agent/memory/ are NOT under runtime/tools/,
    so they can write to the DB freely."""
    fake_repo.write("agent/storage/repo.py", "def f(db):\n    db.commit()\n")
    fake_repo.write("agent/memory/store.py", "def f(db):\n    db.add(row)\n")
    assert check_arch_module.check_rule_2() == []


# ─── RULE 3: cross-domain imports only via service / schemas / __init__ ─


def test_rule3_catches_models_import_across_domain(fake_repo, check_arch_module):
    fake_repo.write(
        "domains/orders/service.py",
        "from domains.masterdata.models import Product\n",
    )
    violations = check_arch_module.check_rule_3()
    assert len(violations) == 1
    assert "orders→masterdata.models" in violations[0].rule


def test_rule3_catches_repository_import_across_domain(fake_repo, check_arch_module):
    fake_repo.write(
        "domains/orders/service.py",
        "from domains.masterdata.repository import find_by_code\n",
    )
    violations = check_arch_module.check_rule_3()
    assert len(violations) == 1
    assert "orders→masterdata.repository" in violations[0].rule


def test_rule3_allows_service_schemas_init_import(fake_repo, check_arch_module):
    """All three legitimate cross-domain import paths must be allowed."""
    fake_repo.write(
        "domains/orders/service.py",
        "from domains.masterdata import service\n"
        "from domains.masterdata.service import list_products\n"
        "from domains.masterdata.schemas import ProductCreate\n",
    )
    assert check_arch_module.check_rule_3() == []


def test_rule3_does_not_fire_for_same_domain(fake_repo, check_arch_module):
    """Inside the same domain you can import your own models/repository freely."""
    fake_repo.write(
        "domains/orders/service.py",
        "from domains.orders.models import Order\n"
        "from domains.orders.repository import find\n",
    )
    assert check_arch_module.check_rule_3() == []


# ─── RULE 4: shared/** must NOT import business layers ─────────


def test_rule4_catches_shared_importing_domain(fake_repo, check_arch_module):
    fake_repo.write("shared/util.py", "from domains.orders import service\n")
    violations = check_arch_module.check_rule_4()
    assert len(violations) == 1
    assert "RULE-4" in violations[0].rule


def test_rule4_catches_shared_importing_apps(fake_repo, check_arch_module):
    fake_repo.write("shared/middleware.py", "from apps.http import deps\n")
    violations = check_arch_module.check_rule_4()
    assert len(violations) == 1


def test_rule4_catches_shared_importing_infrastructure(fake_repo, check_arch_module):
    fake_repo.write("shared/cache.py", "from infrastructure.db import session\n")
    violations = check_arch_module.check_rule_4()
    assert len(violations) == 1


def test_rule4_allows_stdlib_imports_in_shared(fake_repo, check_arch_module):
    fake_repo.write(
        "shared/util.py",
        "import json\nfrom datetime import datetime\nfrom typing import Any\n",
    )
    assert check_arch_module.check_rule_4() == []


# ─── RULE 5: apps/line/** must NOT import apps/http/** ─────────


def test_rule5_catches_line_importing_http(fake_repo, check_arch_module):
    fake_repo.write("apps/line/handlers.py", "from apps.http.deps import get_user\n")
    violations = check_arch_module.check_rule_5()
    assert len(violations) == 1
    assert "RULE-5" in violations[0].rule


def test_rule5_allows_line_importing_domain_or_infra(fake_repo, check_arch_module):
    """Sharing is allowed via domains/infrastructure — only apps/http is forbidden."""
    fake_repo.write(
        "apps/line/handlers.py",
        "from domains.orders import service\n"
        "from infrastructure.security import verify_password\n",
    )
    assert check_arch_module.check_rule_5() == []


# ─── main() exit code ──────────────────────────────────────────


def test_main_returns_0_when_repo_is_clean(fake_repo, check_arch_module, capsys):
    """Empty fake repo → all rules pass → exit 0."""
    rc = check_arch_module.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out


def test_main_returns_1_when_any_violation_exists(fake_repo, check_arch_module, capsys):
    fake_repo.write("domains/x/service.py", "from agent.runtime import deps\n")
    rc = check_arch_module.main()
    out = capsys.readouterr().out
    assert rc == 1
    assert "1 violation" in out


# ─── Real-repo sanity ──────────────────────────────────────────


def test_real_repo_has_zero_violations(check_arch_module):
    """The current v3 codebase MUST pass all 5 rules. If this ever fails,
    something landed in main that violates architecture — fix the code,
    not this test."""
    all_violations = (
        check_arch_module.check_rule_1()
        + check_arch_module.check_rule_2()
        + check_arch_module.check_rule_3()
        + check_arch_module.check_rule_4()
        + check_arch_module.check_rule_5()
    )
    assert all_violations == [], "\n".join(v.format() for v in all_violations)
