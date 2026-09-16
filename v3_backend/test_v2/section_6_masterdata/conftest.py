"""Section-6 (masterdata upload) test fixtures.

The strict identity contract (2026-05-27) requires every upload row to
resolve a real Country + Port row by name. Tests that don't care about
specific values still need the default (`TestCountry` / `TestPort`)
rows to EXIST in the DB so the matcher's FK lookup succeeds. The
default values themselves are decided in `helpers.seed_default_location`;
this fixture just ensures they're seeded before each test runs.

Tests that DO care about specific country/port values (multi-port
probes, FK-error tests) still create their own rows — those values
override the defaults at row-level, so the autouse seed is harmless.
"""

from __future__ import annotations

import pytest

from test_v2.fixtures.helpers import seed_default_location


@pytest.fixture(autouse=True)
def _seed_default_location(db):
    """Auto-seed TestCountry + TestPort so make_excel's default injection
    has FK targets to resolve against."""
    seed_default_location(db)
    yield
