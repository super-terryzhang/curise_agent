"""Opt-in real Gemini evaluation against the verified production port snapshot.

Run explicitly with:
    RUN_REAL_GEMINI_TESTS=1 PYTEST_RUN_SLOW=1 GOOGLE_API_KEY=... \
      pytest test_v2/section_e2e/test_llm_port_resolution_eval.py -v
"""

from __future__ import annotations

import os

import pytest

from domains.orders.port_resolution import PortCandidate, resolve_destination
from infrastructure.config import settings


pytestmark = [
    pytest.mark.skipif(
        not (
            os.getenv("RUN_REAL_GEMINI_TESTS")
            and os.getenv("PYTEST_RUN_SLOW")
            and os.getenv("GOOGLE_API_KEY")
        ),
        reason=(
            "real Gemini port eval requires RUN_REAL_GEMINI_TESTS=1, "
            "PYTEST_RUN_SLOW=1 and GOOGLE_API_KEY"
        ),
    ),
]


# Read-only snapshot verified in reviews/2026-09-09-order-auto-group/ports.json.
# The eval passes IDs only; a product page must always re-read display names.
PRODUCTION_PORT_CANDIDATES = [
    PortCandidate(id=19, name="横浜 大さん橋", country_id=9),
    PortCandidate(id=20, name="神戸", country_id=9),
    PortCandidate(id=21, name="大阪", country_id=9),
    PortCandidate(id=22, name="横浜 大黒ふ頭", country_id=9),
    PortCandidate(id=23, name="福岡", country_id=9),
    PortCandidate(id=24, name="長崎", country_id=9),
    PortCandidate(id=25, name="沖縄", country_id=9),
    PortCandidate(id=26, name="室蘭", country_id=9),
    PortCandidate(id=27, name="函館", country_id=9),
    PortCandidate(id=28, name="東京", country_id=9),
    PortCandidate(id=29, name="SYDNEY", country_id=11),
    PortCandidate(id=30, name="BRISBANE", country_id=11),
    PortCandidate(id=33, name="北海道", country_id=9),
]


@pytest.mark.parametrize(
    ("destination", "expected_status", "expected_port_id"),
    [
        ("OSAKA", "matched", 21),
        ("OKINAWA", "matched", 25),
        ("TOKYO", "matched", 28),
        ("SYDNEY", "matched", 29),
        ("PORTLAND", "unmatched", None),
        ("YOKOHAMA", "unmatched", None),
        ("YOKOHAMA OSANBASHI", "matched", 19),
    ],
)
def test_real_gemini_selects_only_expected_production_port_id(
    destination,
    expected_status,
    expected_port_id,
):
    decision = resolve_destination(
        destination,
        PRODUCTION_PORT_CANDIDATES,
        api_key=os.environ["GOOGLE_API_KEY"],
        model=settings.LLM_PORT_RESOLUTION_MODEL,
        timeout_ms=settings.LLM_PORT_RESOLUTION_TIMEOUT_MS,
        attempts=settings.LLM_PORT_RESOLUTION_ATTEMPTS,
    )

    assert decision.status == expected_status
    assert decision.port_id == expected_port_id
