# Clean Database Bootstrap Design

## Purpose

Create a reproducible, isolated foundation for a brand-new CruiseAgent database without reading or copying production business rows and without changing the current production database connection.

The fresh database must contain the complete current application schema at Alembic head `0036_unified_data_tables`, the three required system data-table catalog rows, and optionally one explicitly supplied superadmin. Every other business table must be empty.

## Why a separate bootstrap is required

The historical migration chain begins with `0001_baseline`, an intentional no-op that assumes the legacy v2 tables already exist. Replaying that chain against a blank PostgreSQL database therefore fails before it can reach current head.

Rewriting historical migrations would put existing installations at risk. The safe approach is a fresh-install snapshot built from the current ORM contract, stamped at the current Alembic head, while preserving the incremental migration chain for databases that already exist.

## Components

1. `infrastructure/db/model_registry.py` is the single list of ORM modules that define application tables. Alembic, tests and the fresh installer use the same registry so a model cannot silently disappear from one path.
2. `scripts/bootstrap_fresh_database.py` installs the current schema into an explicitly confirmed, empty PostgreSQL `public` schema in one transaction.
3. The installer adds the migration-only product-validity audit table so the fresh schema remains compatible with the current migration head, seeds only the three system catalog rows, optionally creates one superadmin, and records the exact Alembic head.
4. `scripts/audit_fresh_database.py` independently checks schema head, required tables, allowed bootstrap rows and zero business rows.

## Safety boundaries

- PostgreSQL only; SQLite and other engines are rejected.
- The URL database name must exactly match `--expected-database`.
- The `public` schema must contain no tables or views before installation.
- Known production-like database names are rejected unless the code is deliberately changed and reviewed.
- No `DROP`, truncate or overwrite mode exists.
- Passwords come from environment variables and are never printed.
- Cloud SQL is created as a separate instance; the current `cruise-v3-db` instance, production secrets and deployed services are not modified.

## Bootstrap data policy

Allowed rows after installation:

- `alembic_version`: one row, `0036_unified_data_tables`.
- `v3_data_tables`: exactly the deterministic system catalog rows for products, suppliers and orders.
- `users`: zero rows by default, or one explicitly requested active superadmin with a default password that must be changed.

All products, suppliers, ports, countries, orders, inquiries, documents, prices, images, uploads, chat records, dynamic records and other business rows must be zero.

## Verification

The same installer is first run twice against disposable local PostgreSQL schemas: once to prove a correct fresh install and once to prove a second run refuses a non-empty target. Targeted tests verify the complete model inventory, seed policy and safety guards. Only after those pass may a separate Tokyo Cloud SQL instance be created and audited with the same scripts.

The work remains on `feature/clean-db-bootstrap-20261006`; it is not merged, pushed, deployed or wired to the production application in this phase.
