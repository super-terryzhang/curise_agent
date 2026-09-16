"""Global pytest configuration for test_v2.

Mirrors the proven fixture setup from `tests/conftest.py` but with cleaner
separation. Section-specific fixtures should live in each section's own
`conftest.py`, not here.

Fixtures provided here:
    engine          fresh in-memory SQLite per test
    session_factory sessionmaker bound to that engine + monkeypatches SessionLocal
    db              ready-to-use Session
    seed_user       a pre-seeded superadmin User row
    client          FastAPI TestClient with DB override
    auth_tokens     logged-in employee, returns login response body (includes JWT)
    sample_pdfs     dict of {name: bytes} for real test PDFs (E2E only)

Autouse fixtures (apply to every test):
    _sync_job_runner    background tasks run synchronously so we can assert
    _local_storage      uploads go to a tmp dir, never production
    _disable_llm_extractor  no real Gemini API calls by default
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

# Env vars must be set before importing project modules
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only")
os.environ.setdefault("ENV", "development")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

# Side-effect imports: register ORM models so create_all sees them
from agent.storage import models as _agent_storage_models  # noqa: F401, E402
from domains.document import models as _document_models  # noqa: F401, E402
from domains.identity.models import User  # noqa: E402
from domains.line import models as _line_models  # noqa: F401, E402
from domains.masterdata import models as _masterdata_models  # noqa: F401, E402
from domains.masterdata.upload import models as _masterdata_upload_models  # noqa: F401, E402
from infrastructure import storage as storage_module  # noqa: E402
from infrastructure.db.base import Base  # noqa: E402
from infrastructure.db.session import get_db  # noqa: E402
from infrastructure.jobs import runner as job_runner  # noqa: E402
from infrastructure.security import hash_password  # noqa: E402
from main import app  # noqa: E402

# ─── Autouse: enforce test-safe defaults ─────────────────────


@pytest.fixture(autouse=True)
def _sync_job_runner():
    """Background pipelines complete synchronously so we can assert on results.

    Without this, BackgroundTasks fire-and-forget and tests see "uploading"
    status forever.
    """
    original = job_runner.get_job_runner()
    runner = job_runner.SynchronousRunner()
    job_runner.set_job_runner(runner)
    yield runner
    job_runner.set_job_runner(original)


@pytest.fixture(autouse=True)
def _local_storage(tmp_path):
    """Uploaded bytes go to a per-test tmp dir, never to production storage."""
    from infrastructure.storage import LocalFileStorage

    storage = LocalFileStorage(tmp_path / "uploads")
    storage_module.set_storage_for_tests(storage)
    yield storage


@pytest.fixture(autouse=True)
def _disable_llm_extractor(monkeypatch):
    """Block real Gemini API calls. Tests that need the LLM override this."""
    monkeypatch.setattr("domains.orders._llm_extractor.settings.GOOGLE_API_KEY", "")


# ─── Core: DB + session ───────────────────────────────────────


@pytest.fixture
def engine():
    """Fresh in-memory SQLite engine per test.

    `StaticPool` keeps one connection alive across sessions — required for
    in-memory SQLite to be visible across FastAPI TestClient's thread pool.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine, monkeypatch) -> sessionmaker[Session]:
    """Per-test sessionmaker. Patches SessionLocal so background pipelines see
    the test DB."""
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    from infrastructure.db import session as session_module

    monkeypatch.setattr(session_module, "SessionLocal", factory)
    return factory


@pytest.fixture
def db(session_factory) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


# ─── User + auth ──────────────────────────────────────────────


@pytest.fixture
def seed_user(db) -> User:
    """A pre-seeded superadmin User (id=1)."""
    user = User(
        email="admin@example.com",
        hashed_password=hash_password("password123"),
        full_name="Admin",
        role="superadmin",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def client(session_factory) -> Generator[TestClient, None, None]:
    """FastAPI TestClient wired to the in-memory DB."""

    def _get_test_db() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_test_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def auth_tokens(client, session_factory):
    """Seed an employee + log in. Returns login response body."""
    session = session_factory()
    try:
        user = User(
            email="tester@example.com",
            hashed_password=hash_password("password123"),
            full_name="Tester",
            role="employee",
            is_active=True,
        )
        session.add(user)
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/api/auth/login",
        json={"email": "tester@example.com", "password": "password123"},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ─── Real PDF samples (E2E) ───────────────────────────────────

# Maps friendly names → real file paths. Source: ../test-orders/
_SAMPLE_PDF_DIR = Path(__file__).resolve().parent.parent.parent / "test-orders"

_SAMPLE_PDF_INDEX: dict[str, str] = {
    "small_po_68331111": "68331111-A.pdf",
    "small_po_68358749": "68358749.pdf",
    "tiny_po_68007850": "PurchaseOrder_68007850.pdf",
    "cci_po_107309": "PO107309CCI-A.pdf",
    "cci_large_po_102292": "PO102292CCI-A_compressed (1).pdf",
    "silver_nova": "Silver Nova - 5111794501-418013.pdf",
    "excel_pdf_cyi": "20251213 PO No CYI-REQ2561 PO01.xlsx  -  Read-Only.pdf",
}


@pytest.fixture(scope="session")
def sample_pdfs() -> dict[str, bytes]:
    """Map of {name: file_bytes} for real cruise procurement PDFs.

    Loaded once per session for E2E tests. The files contain controlled business
    data and are not stored in the public repository, so the entire dependent
    test group is skipped unless all named samples are available locally.
    """
    out: dict[str, bytes] = {}
    for name, fname in _SAMPLE_PDF_INDEX.items():
        path = _SAMPLE_PDF_DIR / fname
        if path.exists():
            out[name] = path.read_bytes()
    missing = sorted(set(_SAMPLE_PDF_INDEX) - set(out))
    if missing:
        pytest.skip(
            "controlled real PDF fixtures are unavailable: " + ", ".join(missing)
        )
    return out
