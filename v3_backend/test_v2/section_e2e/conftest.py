"""Section E2E — fixtures specific to end-to-end workflow tests.

E2E tests upload real cruise PDFs from `test-orders/` and verify the full
pipeline: file upload → document classification → order projection → inquiry
generation → file download.

LLM is blocked by the autouse `_disable_llm_extractor` fixture at the suite
root. E2E tests verify the pipeline ORCHESTRATION; extraction quality is
covered by Section 4 (with stubbed LLM) and would belong in a separate
'real LLM eval suite' if we ever add one.
"""

from __future__ import annotations

import pytest

from test_v2.fixtures.helpers import login, seed_user


@pytest.fixture
def employee_auth(client, db) -> dict[str, str]:
    """Employee user + auth headers — most workflow tests run as employee."""
    seed_user(db, email="e2e_emp@x.test", role="employee", password="password123")
    return login(client, "e2e_emp@x.test")


@pytest.fixture
def admin_auth(client, db) -> dict[str, str]:
    """Admin user + auth headers — for masterdata + settings paths."""
    seed_user(db, email="e2e_adm@x.test", role="admin", password="password123")
    return login(client, "e2e_adm@x.test")
