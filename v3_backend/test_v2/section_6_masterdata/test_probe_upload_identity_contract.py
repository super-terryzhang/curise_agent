"""Probe suite — what does upload matching ACTUALLY do (2026-05-27)?

Background:
    The system has 4 inconsistent definitions of "product identity":
      - DB unique constraint: (country, name, port)
      - resolve_and_score Rules 1+2: (code|name, country, port)
      - resolve_and_score Rules 3+4: code or name (global, legacy)
      - User-facing docs: code-or-name-only
      - Agent template tool docstring: doesn't even mention country/port

    Before designing the fix, we need EMPIRICAL truth about each rule's
    behaviour in every (DB state, Excel state) combination. This probe
    file is that ground-truth survey.

    Each probe asserts the BUSINESS-CORRECT behaviour for its scenario.
    A failing probe = a behavioural gap. We expect several probes to
    fail today — that's the point. The pattern of failures tells us
    which rules need surgery and where.

Reading the output:
    PASSED  = system already behaves correctly for this scenario
    FAILED  = system silently misbehaves; the error message describes
              what the system actually did vs what was expected
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from domains.masterdata.models import Country, Port
from domains.masterdata.upload import parse_excel, resolve_and_score
from domains.masterdata.upload.models import StagingProduct
from test_v2.fixtures.helpers import make_excel, seed_product


# ─── helpers ────────────────────────────────────────────────


def _seed_country(db: Session, name: str) -> int:
    c = Country(name=name)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c.id


def _seed_port(db: Session, name: str, country_id: int) -> int:
    p = Port(name=name, country_id=country_id)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p.id


def _stage_and_resolve(db: Session, row: dict, user_id: int = 1) -> StagingProduct:
    blob = make_excel([row])
    batch = parse_excel(db, file_bytes=blob, filename="probe.xlsx", user_id=user_id)
    resolve_and_score(db, batch_id=batch.id, user_id=user_id)
    return db.query(StagingProduct).filter(StagingProduct.batch_id == batch.id).one()


def _describe(sp: StagingProduct) -> str:
    """Compact one-line description of a staging row's resolution outcome."""
    return (
        f"status={sp.match_status!r} target_id={sp.match_target_id!r} "
        f"confidence={sp.confidence!r}"
    )


# ─── P1: empty DB, minimal row → new ────────────────────────


def test_P1_empty_db_yields_new(db: Session) -> None:
    """Baseline: nothing in DB. Excel row is always new."""
    sp = _stage_and_resolve(db, {"product_code": "X-001", "product_name": "Widget"})
    assert sp.match_status == "new", _describe(sp)


# ─── P2: single legacy row, no country/port → exact ────────


def test_P2_legacy_unique_code_no_location_matches_exact(db: Session) -> None:
    """Single-port legacy customer: 1 row in DB with no country/port set,
    Excel row has no country/port either. Code is globally unique →
    Rule 3 (code-only legacy fallback) should match. Expected behaviour
    for backwards compat."""
    target = seed_product(db, code="X-001", name="Widget", price=10.0)
    sp = _stage_and_resolve(db, {"product_code": "X-001", "product_name": "Widget"})
    assert sp.match_status == "exact" and sp.match_target_id == target.id, _describe(sp)


# ─── P3: DB has Sydney row, Excel says Tokyo → should be NEW ─


def test_P3_single_db_row_excel_specifies_different_port(db: Session) -> None:
    """DB has the product at Sydney. Excel uploads to Tokyo. The user is
    saying "create a new Tokyo row". System currently misroutes via
    Rule 3 (code-only fallback) → UPDATEs Sydney's port to Tokyo on
    commit. This is the headline Bug A."""
    japan = _seed_country(db, "Japan")
    _seed_port(db, "Sydney", japan)
    _seed_port(db, "Tokyo", japan)
    seed_product(db, code="99PRD010725", name="Apple Red", country_id=japan, price=100.0)
    sp = _stage_and_resolve(db, {
        "product_code": "99PRD010725",
        "product_name": "Apple Red",
        "country": "Japan",
        "port": "Tokyo",
        "price": 150.0,
    })
    assert sp.match_status == "new", (
        f"Excel-specified port ≠ DB row's port → should be NEW. {_describe(sp)}"
    )


# ─── P4: DB has 2 rows same code, Excel no port → ambiguous/new ─


def test_P4_db_has_multiple_same_code_no_port_in_excel(db: Session) -> None:
    """DB has the SAME code at two ports. Excel doesn't specify port.
    The right behaviour is either:
       (a) status='new' (system can't disambiguate; leave it to user)
       (b) a future 'ambiguous' status
    What MUST NOT happen: silently picking one of the two existing
    rows. The user has no idea which one would be overwritten."""
    japan = _seed_country(db, "Japan")
    sydney = _seed_port(db, "Sydney", japan)
    auckland = _seed_port(db, "Auckland", japan)
    seed_product(db, code="X-001", name="W", country_id=japan, port_id=sydney, price=100)
    seed_product(db, code="X-001", name="W", country_id=japan, port_id=auckland, price=120)
    sp = _stage_and_resolve(db, {"product_code": "X-001", "product_name": "W"})
    assert sp.match_status == "new", (
        f"Code has 2+ hits AND Excel lacks port → must not silently match. {_describe(sp)}"
    )


# ─── P5: DB has 2 rows same code, Excel says new port → new ─


def test_P5_db_has_multiple_same_code_excel_specifies_third_port(db: Session) -> None:
    """DB has Sydney + Auckland rows. Excel uploads to Tokyo (a third
    port). Expected: new row at Tokyo. Anything matching Sydney or
    Auckland is silent corruption."""
    japan = _seed_country(db, "Japan")
    sydney = _seed_port(db, "Sydney", japan)
    auckland = _seed_port(db, "Auckland", japan)
    _seed_port(db, "Tokyo", japan)
    seed_product(db, code="X-001", name="W", country_id=japan, port_id=sydney, price=100)
    seed_product(db, code="X-001", name="W", country_id=japan, port_id=auckland, price=120)
    sp = _stage_and_resolve(db, {
        "product_code": "X-001",
        "product_name": "W",
        "country": "Japan",
        "port": "Tokyo",
    })
    assert sp.match_status == "new", (
        f"Two existing rows at other ports, Excel says Tokyo → new. {_describe(sp)}"
    )


# ─── P6: DB row matches exactly on (code, country, port) → exact ─


def test_P6_full_key_match_updates_correctly(db: Session) -> None:
    """The happy path: Excel updates a product at its own port. Rule 1
    should fire. This is the contract Rule 1 was added for."""
    japan = _seed_country(db, "Japan")
    tokyo = _seed_port(db, "Tokyo", japan)
    target = seed_product(
        db, code="X-001", name="W", country_id=japan, port_id=tokyo, price=100
    )
    sp = _stage_and_resolve(db, {
        "product_code": "X-001",
        "product_name": "W",
        "country": "Japan",
        "port": "Tokyo",
        "price": 200,
    })
    assert sp.match_status == "exact" and sp.match_target_id == target.id, _describe(sp)


# ─── P7: 1 row at Sydney with no code, Excel name only, Tokyo → new ─


def test_P7_single_name_only_db_row_excel_different_port(db: Session) -> None:
    """DB has 1 name-keyed row at Sydney (no code). Excel uploads to
    Tokyo by name only. Expected: new — different port. Currently
    Rule 4 (name-only fallback, no location guard, no len==1 guard)
    misroutes."""
    japan = _seed_country(db, "Japan")
    sydney = _seed_port(db, "Sydney", japan)
    _seed_port(db, "Tokyo", japan)
    seed_product(db, code=None, name="Banana", country_id=japan, port_id=sydney, price=100)
    sp = _stage_and_resolve(db, {
        "product_name": "Banana",
        "country": "Japan",
        "port": "Tokyo",
    })
    assert sp.match_status == "new", (
        f"Different port → new, not name_exact pointing at Sydney. {_describe(sp)}"
    )


# ─── P8: 2 rows same name diff ports, Excel name only → ambiguous/new ─


def test_P8_multiple_names_no_port_in_excel(db: Session) -> None:
    """DB has the same name at two ports. Excel says only name. System
    has no basis to pick one over the other — must NOT silently match.
    Expected: status='new' (leave it to user to disambiguate)."""
    japan = _seed_country(db, "Japan")
    sydney = _seed_port(db, "Sydney", japan)
    auckland = _seed_port(db, "Auckland", japan)
    seed_product(db, code=None, name="Banana", country_id=japan, port_id=sydney)
    seed_product(db, code=None, name="Banana", country_id=japan, port_id=auckland)
    sp = _stage_and_resolve(db, {"product_name": "Banana"})
    assert sp.match_status == "new", (
        f"Name has 2+ hits AND Excel lacks port → must not silently pick. {_describe(sp)}"
    )


# ─── P9: full key match wins over fallback ──────────────────


def test_P9_full_key_wins_when_legacy_would_misfire(db: Session) -> None:
    """DB has Tokyo row + Sydney row, same code. Excel says Tokyo.
    Rule 1 should fire on the Tokyo row. (Code-only fallback would
    skip because code has >1 hits, so Rule 3 wouldn't misfire here
    anyway — but pin the contract.)"""
    japan = _seed_country(db, "Japan")
    tokyo = _seed_port(db, "Tokyo", japan)
    sydney = _seed_port(db, "Sydney", japan)
    tokyo_row = seed_product(
        db, code="X-001", name="W", country_id=japan, port_id=tokyo, price=100
    )
    seed_product(
        db, code="X-001", name="W", country_id=japan, port_id=sydney, price=120
    )
    sp = _stage_and_resolve(db, {
        "product_code": "X-001",
        "product_name": "W",
        "country": "Japan",
        "port": "Tokyo",
    })
    assert sp.match_status == "exact" and sp.match_target_id == tokyo_row.id, (
        f"Should land on Tokyo row, not Sydney. {_describe(sp)}"
    )


# ─── P10: DB row at Sydney (no port_id), Excel says Tokyo → new ─


def test_P10_db_row_without_port_excel_with_port_should_be_new(db: Session) -> None:
    """DB has a row with country=Japan but port_id=NULL (legacy data
    without port). Excel says country=Japan, port=Tokyo. The Excel
    upload is creating a port-specific record where there was only
    a country-level record. Expected behaviour is debatable: could be
    'update the existing row with a port', could be 'create new'. Pin
    the current behaviour by asserting one of the two safe outcomes —
    anything that loses the original row is bad."""
    japan = _seed_country(db, "Japan")
    _seed_port(db, "Tokyo", japan)
    legacy = seed_product(
        db, code="X-001", name="W", country_id=japan, price=100
    )  # NOTE: no port_id set
    sp = _stage_and_resolve(db, {
        "product_code": "X-001",
        "product_name": "W",
        "country": "Japan",
        "port": "Tokyo",
    })
    # Allow either outcome but document the actual behaviour explicitly.
    assert sp.match_status in {"new", "exact"}, _describe(sp)
    if sp.match_status == "exact":
        assert sp.match_target_id == legacy.id, (
            f"If matching legacy row, must match the actual legacy row, not "
            f"something else. {_describe(sp)}"
        )
