"""Explicit local-only synthetic acceptance environment; never clears existing data.

Run from v3_backend. The dedicated cluster is owned by this development round.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from infrastructure.config import settings


def main():
    url = make_url(settings.DATABASE_URL)
    if (
        url.get_backend_name() != "postgresql"
        or url.host != "127.0.0.1"
        or url.port != 55447
        or url.database != "cruise_data_tables_test"
        or dict(url.query) != {"options": "-csearch_path=custom_demo_20261006"}
    ):
        raise RuntimeError("Acceptance setup only allows the explicit owned localhost demo schema")
    plain = create_engine(url.difference_update_query(["options"]))
    with plain.begin() as conn:
        exists = conn.scalar(
            text("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='custom_demo_20261006')")
        )
        if exists:
            raise RuntimeError("Demo schema already exists; reuse it, never reset or overwrite")
        conn.execute(text("CREATE SCHEMA custom_demo_20261006"))
    plain.dispose()
    import main as application  # noqa: F401
    from domains.identity.models import User
    from infrastructure.db.base import Base
    from infrastructure.db.engine import engine
    from infrastructure.security import hash_password

    # Legacy baseline is a no-op: explicitly prepare its old schema snapshot.
    Base.metadata.create_all(
        engine, tables=[t for n, t in Base.metadata.tables.items() if not n.startswith("v3_data_")]
    )
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.stamp(config, "0034_drop_product_validity")
    command.upgrade(config, "0035_custom_data_tables")
    with Session(engine) as db:
        db.add(
            User(
                email="custom-admin@example.test",
                full_name="本地验收管理员",
                role="superadmin",
                is_active=True,
                is_default_password=False,
                hashed_password=hash_password("CustomTables!2026"),
            )
        )
        db.add(
            User(
                email="custom-writer@example.test",
                full_name="本地验收员工",
                role="employee",
                is_active=True,
                is_default_password=False,
                hashed_password=hash_password("CustomTables!2026"),
            )
        )
        db.commit()
    assert len([n for n in inspect(engine).get_table_names() if n.startswith("v3_data_")]) == 6
    print(
        "Local synthetic demo ready: schema custom_demo_20261006; custom-admin@example.test / CustomTables!2026"
    )


if __name__ == "__main__":
    main()
