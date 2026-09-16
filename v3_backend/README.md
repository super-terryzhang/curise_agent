# Cruise Backend v3

v3 rebuild of the cruise procurement backend. See [`PLAN.md`](./PLAN.md) for the 9-phase
execution plan and [`AGENT_JOURNAL.md`](./AGENT_JOURNAL.md) for the live progress log.

## Status

**Phase 0 — 地基**：skeleton, auth, CI/CD, ADRs. See `PLAN.md` §3 for the current state.

## Quickstart (local, 30 min)

### 1. Python + venv

```bash
cd curise_agent/v3_backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Environment

```bash
cp .env.example .env
# For local dev, the SQLite default in .env.example works out of the box.
# No changes needed for Phase 0.
```

### 3. Create tables (SQLite, dev only)

```bash
python - <<'PY'
from infrastructure.db.base import Base
from infrastructure.db.engine import engine
from domains.identity import models  # noqa: F401 (registers mappers)
Base.metadata.create_all(bind=engine)
print("tables created:", list(Base.metadata.tables))
PY
```

Phase 1+ uses Alembic migrations — never call `create_all` in production.

### 4. Seed an admin user

```bash
python - <<'PY'
from infrastructure.db.session import SessionLocal
from infrastructure.security import hash_password
from domains.identity.models import User

with SessionLocal() as db:
    if not db.query(User).filter_by(email="admin@example.com").first():
        db.add(User(
            email="admin@example.com",
            hashed_password=hash_password("adminpassword"),
            full_name="Admin",
            role="superadmin",
            is_active=True,
        ))
        db.commit()
        print("seeded admin@example.com / adminpassword")
    else:
        print("admin already exists")
PY
```

### 5. Run

```bash
uvicorn main:app --reload --port 8000
```

### 6. Verify

- Swagger: http://localhost:8000/docs
- Health: `curl http://localhost:8000/health`
- Login: `curl -X POST http://localhost:8000/api/auth/login -H 'Content-Type: application/json' -d '{"email":"admin@example.com","password":"adminpassword"}'`

## Tests

```bash
pytest tests/unit           # fast, no I/O
pytest tests/integration    # with SQLite in-memory DB
pytest tests/                # all
```

## Quality checks

```bash
ruff check .                         # lint
ruff format --check .                # formatting
mypy domains/ infrastructure/ apps/  # types
python scripts/check_arch.py         # module boundaries (ADR-0006)
```

## Architecture

See [`PLAN.md`](./PLAN.md) for the full target structure and the 9-phase plan.

Key points (see `docs/adr/` for full rationale):

- **Document is the parent type; Order is one of its subtypes** (ADR-0001)
- **Agent calls business via service contracts, never the DB directly** (ADR-0002)
- **API contract frozen to v2's current shape** (ADR-0003)
- **v3 shares Supabase with v2; schema evolves expand/contract** (ADR-0004)
- **Background tasks go through `apps/jobs/runner.py`** (ADR-0005)
- **Module boundaries enforced by `scripts/check_arch.py`** (ADR-0006)

## For new contributors

1. Read `PLAN.md` §0–3 (why we're doing this, and where we are now)
2. Read the ADR you'll be touching (`docs/adr/`)
3. Check `AGENT_JOURNAL.md` for the live state of current Phase work
4. Follow the Definition of Done for the current Phase (in PLAN.md)
