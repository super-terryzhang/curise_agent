"""Inspect legacy evidence; pass --apply only during the approved migration window."""

import argparse
import json

from sqlalchemy import text

from domains.masterdata.price_history_backfill import import_legacy
from infrastructure.db.session import SessionLocal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        if (
            db.scalar(text("SELECT version_num FROM alembic_version"))
            != "0025_product_price_history"
        ):
            raise SystemExit(
                "Apply migration 0025 before inspecting/importing legacy price evidence"
            )
        try:
            result = import_legacy(db, apply=args.apply)
            if args.apply:
                db.commit()
            else:
                db.rollback()
            print(json.dumps(result))
        except Exception:
            db.rollback()
            raise


if __name__ == "__main__":
    main()
