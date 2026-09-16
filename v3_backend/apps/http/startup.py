"""Production starts only after an explicit, successful schema release step."""

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from infrastructure.config import settings


def verify_schema(engine) -> None:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "migrations")
    )
    expected = set(ScriptDirectory.from_config(config).get_heads())
    try:
        with engine.connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads())
    except Exception:
        raise RuntimeError(
            "Database schema verification failed; check release migration and connectivity"
        ) from None
    if current != expected:
        raise RuntimeError("Database migration required before serving this application version")


@asynccontextmanager
async def lifespan(app):
    recovery_task = None
    if settings.ENV in ("production", "staging"):
        from infrastructure.db.engine import engine

        verify_schema(engine)
        from apps.jobs.inquiry_jobs import recovery_loop

        recovery_task = asyncio.create_task(
            recovery_loop(), name="arrangement-inquiry-recovery"
        )
    try:
        yield
    finally:
        if recovery_task is not None:
            recovery_task.cancel()
            with suppress(asyncio.CancelledError):
                await recovery_task
