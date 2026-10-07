# Clean Database Bootstrap Verification — 2026-10-07

## Outcome

A separate Cloud SQL foundation was created without changing the existing production database, deployed services or application environment variables.

- Instance: `cruise-v3-db-clean-20261006`
- Project/region/zone: `cruise-v3-prod` / `asia-northeast1` / `asia-northeast1-b`
- Connection name: `cruise-v3-prod:asia-northeast1:cruise-v3-db-clean-20261006`
- Database: `cruise_v3_clean`
- Database owner/bootstrap user: `cruise_v3_clean_app`
- Initial application administrator: `clean-admin@cruise.local`
- PostgreSQL: 17, Enterprise, zonal `db-g1-small`, 10 GB PD_SSD
- Protection: backups enabled at 18:00 UTC, PITR enabled, seven-day transaction-log retention, storage auto-resize enabled, deletion protection enabled

The instance is RUNNABLE. The application is **not** connected to it.

## Source isolation

- Worktree: `curise_agent/.worktrees/clean-db-bootstrap-20261006`
- Branch: `feature/clean-db-bootstrap-20261006`
- Functional commit: `9eaacc7cd2455757f2bc1dcedca6786956738e70`
- Base: local `feature/custom-data-tables-20261006@323acab`
- Not merged, not pushed and not deployed
- Main worktree remained `main@3c9c69a`, clean and ahead of origin by its pre-existing three commits

The fresh installer refuses non-PostgreSQL URLs, a database-name mismatch, protected database names and any target containing an existing table or view. It has no overwrite, truncate or drop mode.

## Installed state

Independent cloud audit result:

```text
Fresh database audit passed: database=cruise_v3_clean
head=0036_unified_data_tables tables=53
system_catalog_rows=3 admin_rows=1 business_rows=0
```

The 53 tables are 51 current application model tables, the retained product-validity migration audit table and `alembic_version`. Allowed bootstrap rows are limited to:

- `alembic_version`: `0036_unified_data_tables`
- `v3_data_tables`: system catalog for products, suppliers and orders
- `users`: the one initial superadmin, marked as using a default password

Products, suppliers, countries, ports, prices, images, documents, orders, inquiries, uploads, chat data, custom records and all other business tables were verified at zero rows. The application's own `verify_schema()` startup guard also passed against the new database.

## Test evidence

- TDD red state: seven tests initially failed because the registry and installer did not exist.
- Targeted unit plus real PostgreSQL verification: `10 passed`.
- Related migration/startup tests: `51 passed`.
- One complete backend regression: `2044 passed, 105 skipped, 0 failed` in 548.66 seconds.
- Ruff on all changed Python files: no findings.
- `git diff --check`: clean.

The real PostgreSQL test created a randomly named disposable database, installed from zero, passed the independent audit, proved that a second bootstrap is refused, inserted a pollution sentinel, proved the audit reports the exact polluted table, and removed the disposable database.

Existing warnings remain unchanged: passlib/bcrypt version metadata, Starlette/FastAPI deprecations, duplicate HEAD operation ID and Alembic `path_separator`. They did not fail the release checks and are not introduced by the database design.

## Secret handling

No password was printed or committed. Secret Manager contains:

- `cruise-v3-clean-db-password-20261006`
- `cruise-v3-clean-initial-admin-password-20261006`

No production service account was granted these secrets and no production environment variable was changed.

## Existing production isolation check

After creation, both instances were read back:

- Existing `cruise-v3-db`: RUNNABLE, PostgreSQL 17, Enterprise, Tokyo, `db-g1-small`, 10 GB; unchanged by this work.
- New `cruise-v3-db-clean-20261006`: RUNNABLE with deletion protection enabled.

The local Cloud SQL Auth Proxy was stopped after verification and port 55449 no longer listens.

## Deliberately deferred

1. Do not point the current production backend at this database. Production is still on schema 0034; the new database is on the unmerged 0036 foundation.
2. Merge/release of unified data tables remains a separate reviewed deployment.
3. Create a least-privilege runtime role and wire a service-specific database URL only when a non-production application environment is connected.
4. Design and implement the temporary database-management/data-upload page after the upload contract is agreed.
5. Decide and execute the old-to-new migration strategy; no production business data was copied.
6. If the instance is only needed for the agreed ten-day evaluation, review and delete it around 2026-10-17 to stop charges. Deletion protection must be deliberately disabled first.
