"""Test helper utilities shared across sections.

Lives in fixtures/ so it's importable from any section via:

    from test_v2.fixtures.helpers import make_excel, make_minimal_pdf
"""

from __future__ import annotations

import io
from typing import Any


def make_excel(rows: list[dict[str, Any]]) -> bytes:
    """Build an in-memory .xlsx file from a list of row dicts.

    Column order = keys of the first row's effective key set. Used by
    masterdata upload tests that don't need a real Excel file but still
    want valid xlsx bytes.

    **Strict identity contract (2026-05-27)** — the upload pipeline now
    requires every row to specify `country` + `port`. To avoid forcing
    every test that doesn't care about location to repeat the defaults,
    rows that mention NEITHER key get the defaults auto-injected. Rows
    that mention either key (even with an empty value) are passed
    through untouched — that's the escape hatch for tests that pin the
    "missing port → row-level error" behaviour.

    >>> blob = make_excel([{"product_code": "A1", "price": 10}, {...}])
    >>> # → row gets {"country": "TestCountry", "port": "TestPort"} injected
    >>> blob = make_excel([{"product_code": "A1", "country": "Japan", "port": "Tokyo"}])
    >>> # → explicit location, no injection
    >>> blob = make_excel([{"product_code": "A1", "port": ""}])
    >>> # → row presents port (empty) — no injection; resolves as row-level error
    """
    from openpyxl import Workbook

    if not rows:
        raise ValueError("make_excel needs at least one row")

    augmented_rows: list[dict[str, Any]] = []
    for r in rows:
        # Inject defaults PER FIELD — independently for country and port.
        # If the row mentions `country` (even with an empty value) it's
        # the test's explicit choice; same for `port`. This keeps the
        # injection narrow: tests that supply only one half (e.g. only
        # `country` because they're testing FK resolution) still get the
        # other half filled in so the strict identity contract is met.
        injected = dict(r)
        if "country" not in injected:
            injected["country"] = _DEFAULT_COUNTRY_NAME
        if "port" not in injected:
            injected["port"] = _DEFAULT_PORT_NAME
        augmented_rows.append(injected)

    wb = Workbook()
    ws = wb.active
    # Use the union of keys across all augmented rows so the auto-
    # injected `country` / `port` columns appear even when the first row
    # didn't originally have them. Preserve first-occurrence order.
    headers: list[str] = []
    seen: set[str] = set()
    for r in augmented_rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                headers.append(k)
    ws.append(headers)
    for r in augmented_rows:
        ws.append([r.get(h) for h in headers])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_minimal_pdf(text: str = "Hello PDF") -> bytes:
    """Build a tiny valid PDF containing one line of text.

    Used for upload tests that need a real PDF binary but don't care about
    content. For tests that need to verify extraction of specific fields,
    use the real samples in fixtures/samples/ via the `sample_pdfs` fixture.
    """
    # PDF 1.4 minimal: catalog → pages → page → content stream with one Tj
    body = f"BT /F1 12 Tf 50 750 Td ({text}) Tj ET".encode("latin-1", errors="replace")
    obj4_stream = body
    obj4 = b"<< /Length %d >>\nstream\n" % len(obj4_stream) + obj4_stream + b"\nendstream"

    parts = [
        b"%PDF-1.4\n",
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >> endobj\n",
        b"4 0 obj " + obj4 + b" endobj\n",
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    xref_pos = sum(len(p) for p in parts)
    out = b"".join(parts)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    pos = 0
    for p in parts[1:]:
        out += f"{pos:010d} 00000 n \n".encode()
        pos += len(parts[0]) if p is parts[1] else 0
    out += b"trailer << /Size 6 /Root 1 0 R >>\n"
    out += f"startxref\n{xref_pos}\n%%EOF\n".encode()
    return out


_DEFAULT_COUNTRY_NAME = "TestCountry"
_DEFAULT_PORT_NAME = "TestPort"


def seed_default_location(db) -> tuple[int, int]:
    """Idempotent: ensure the default `TestCountry` + `TestPort` rows
    exist and return their IDs.

    Most tests that touch the upload pipeline don't care about which
    country/port — they care about the matching / commit / rollback
    behaviour. Since the strict identity contract (2026-05-27) requires
    every product to have a (country, port), tests use these defaults
    to satisfy the contract without inventing fixtures inline.

    Tests that DO care about specific country/port values (e.g. the
    multi-port misroute probes) create their own Country + Port rows.
    """
    from domains.masterdata.models import Country, Port

    c = db.query(Country).filter_by(name=_DEFAULT_COUNTRY_NAME).first()
    if c is None:
        c = Country(name=_DEFAULT_COUNTRY_NAME)
        db.add(c)
        db.commit()
        db.refresh(c)
    p = (
        db.query(Port)
        .filter_by(name=_DEFAULT_PORT_NAME, country_id=c.id)
        .first()
    )
    if p is None:
        p = Port(name=_DEFAULT_PORT_NAME, country_id=c.id)
        db.add(p)
        db.commit()
        db.refresh(p)
    return c.id, p.id


def default_location_row_fields() -> dict[str, str]:
    """The `country` + `port` cells to inject into an Excel test row to
    satisfy the strict identity contract. Use as
        make_excel([{**default_location_row_fields(), "product_name": ...}])
    """
    return {"country": _DEFAULT_COUNTRY_NAME, "port": _DEFAULT_PORT_NAME}


_SENTINEL = object()


def seed_product(
    db,
    *,
    code: str = "TEST-001",
    name: str = "Test Product",
    price: float = 10.0,
    country_id=_SENTINEL,
    supplier_id: int | None = None,
    category_id: int | None = None,
    port_id=_SENTINEL,
    unit: str = "KG",
):
    """Insert a Product row with sensible defaults. Returns the persisted obj.

    `country_id` / `port_id` default to `seed_default_location(db)` when
    the caller doesn't specify them — the strict identity contract
    (2026-05-27) requires every product to have a (country, port), and
    most tests don't care about which specific values are used. To opt
    out (e.g. for legacy-row tests), pass `country_id=None` /
    `port_id=None` explicitly.
    """
    from decimal import Decimal

    from domains.masterdata.models import Product

    if country_id is _SENTINEL or port_id is _SENTINEL:
        default_cid, default_pid = seed_default_location(db)
        if country_id is _SENTINEL:
            country_id = default_cid
        if port_id is _SENTINEL:
            port_id = default_pid

    p = Product(
        code=code,
        product_name_en=name,
        product_name_jp=None,
        price=Decimal(str(price)),
        unit=unit,
        country_id=country_id,
        supplier_id=supplier_id,
        category_id=category_id,
        port_id=port_id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def seed_user(db, *, email: str, role: str = "employee", password: str = "password123"):
    """Insert a User. Returns the persisted obj."""
    from domains.identity.models import User
    from infrastructure.security import hash_password

    u = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=email.split("@")[0],
        role=role,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def login(client, email: str, password: str = "password123") -> dict[str, str]:
    """Log in via the API. Returns Authorization header dict ready to use."""
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login failed: {r.text}"
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
