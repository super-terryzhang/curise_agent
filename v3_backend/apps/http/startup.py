"""Production starts only after an explicit, successful schema release step."""

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from infrastructure.config import settings


def verify_database_setup_target(engine) -> None:
    """Fail closed if the temporary setup module points anywhere but its clean DB."""
    if not settings.TEMP_DATABASE_SETUP_ENABLED:
        return
    if settings.TEMP_DATABASE_SETUP_EXPECTED_DATABASE != "cruise_v3_clean":
        raise RuntimeError("Temporary database setup expected clean database is invalid")
    try:
        with engine.connect() as connection:
            actual = connection.scalar(text("SELECT current_database()"))
    except Exception:
        raise RuntimeError("Temporary database setup could not verify clean database") from None
    if actual != "cruise_v3_clean":
        raise RuntimeError("Temporary database setup must use the exact clean database")


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
    if settings.SCHEMA_RELEASE_TRANSITION:
        if (
            settings.SCHEMA_RELEASE_TRANSITION != "unified_data_tables_0034_0036"
            or settings.CUSTOM_DATA_TABLES_ENABLED
            or expected != {"0036_unified_data_tables"}
            or current
            not in (
                {"0034_drop_product_validity"},
                {"0035_custom_data_tables"},
                {"0036_unified_data_tables"},
            )
        ):
            raise RuntimeError("Invalid schema release transition or database head")
        return
    if current != expected:
        raise RuntimeError("Database migration required before serving this application version")


@asynccontextmanager
async def lifespan(app):
    recovery_task = None
    if settings.TEMP_DATABASE_SETUP_ENABLED:
        from infrastructure.db.engine import engine

        verify_database_setup_target(engine)
    if settings.ENV in ("production", "staging"):
        from infrastructure.db.engine import engine

        verify_schema(engine)
        from apps.jobs.inquiry_jobs import recovery_loop

        recovery_task = asyncio.create_task(recovery_loop(), name="arrangement-inquiry-recovery")
    try:
        yield
    finally:
        if recovery_task is not None:
            recovery_task.cancel()
            with suppress(asyncio.CancelledError):
                await recovery_task
