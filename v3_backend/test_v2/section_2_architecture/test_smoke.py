"""Smoke test: verify test_v2 conftest + helpers wire up correctly.

If this fails, the whole test_v2 layout is misconfigured. Fix this first
before running anything else.
"""

from __future__ import annotations


def test_db_fixture_works(db):
    """The `db` fixture from test_v2/conftest.py should provide a Session."""
    from sqlalchemy import text

    result = db.execute(text("SELECT 1")).scalar()
    assert result == 1


def test_helpers_importable():
    """Helpers module should be importable from any section."""
    from test_v2.fixtures.helpers import make_excel, make_minimal_pdf, seed_user  # noqa: F401


def test_client_fixture_works(client):
    """TestClient should respond to /health."""
    r = client.get("/health")
    assert r.status_code == 200


def test_health_exposes_deployment_metadata(client):
    """`/health` must surface service + revision so a user pasting a
    bug report can be tied to the exact Cloud Run deployment. Outside
    Cloud Run (CI / local) both fall back to "local"."""
    r = client.get("/health")
    payload = r.json()
    assert payload["status"] == "ok"
    assert payload["version"] == "3.0.0"
    # Keys must always exist; the values may be "local" outside Cloud Run.
    assert "service" in payload
    assert "revision" in payload


def test_auth_tokens_fixture_works(auth_tokens):
    """The auth_tokens fixture should produce a usable JWT."""
    assert "access_token" in auth_tokens
    assert len(auth_tokens["access_token"]) > 20


def test_sample_pdfs_fixture(sample_pdfs):
    """All 7 sample PDFs should be present and non-empty."""
    expected = {
        "small_po_68331111",
        "small_po_68358749",
        "tiny_po_68007850",
        "cci_po_107309",
        "cci_large_po_102292",
        "silver_nova",
        "excel_pdf_cyi",
    }
    assert set(sample_pdfs.keys()) == expected, (
        f"missing PDFs: {expected - set(sample_pdfs.keys())}"
    )
    for name, blob in sample_pdfs.items():
        assert blob.startswith(b"%PDF"), f"{name} doesn't look like a PDF"
        assert len(blob) > 1000, f"{name} suspiciously small ({len(blob)} bytes)"
