# Clean Database Bootstrap Implementation Plan

> Execute in the isolated `feature/clean-db-bootstrap-20261006` worktree. Do not change production connections, deploy application code, merge, or push.

## Task 1: Freeze the complete model inventory

**Files:** `v3_backend/infrastructure/db/model_registry.py`, `v3_backend/migrations/env.py`, targeted tests.

Create one import registry containing every ORM module, including chat storage, Oracle scan and order financial models. Verify that the registered inventory contains all current application tables and that Alembic consumes the same registry.

**Acceptance:** the inventory test detects a missing module and reports the expected table set; existing migration tests remain green.

## Task 2: Implement fail-closed fresh installation

**Files:** `v3_backend/scripts/bootstrap_fresh_database.py`, targeted tests.

Add a PostgreSQL-only installer that checks the explicit database name, refuses a non-empty `public` schema, creates the current schema transactionally, adds the migration-only audit table, seeds only system catalog rows, optionally creates one superadmin from environment-only credentials, and stamps the single Alembic head.

**Acceptance:** a blank PostgreSQL schema installs successfully; a second run, wrong database name, non-PostgreSQL URL or pre-existing table is rejected without deleting anything.

## Task 3: Add independent empty-database audit

**Files:** `v3_backend/scripts/audit_fresh_database.py`, targeted tests.

Build a read-only audit that compares installed tables and head with the expected foundation and reports any unexpected business rows. The audit must not share mutation code with the installer.

**Acceptance:** the clean database passes; inserting one product/order/custom record makes the audit fail with the exact table and row count.

## Task 4: Validate locally with real PostgreSQL

Use a disposable local PostgreSQL container/database, run targeted tests and the install/audit commands, then run the related migration/integration tests. Destroy only the disposable local target after evidence is recorded.

**Acceptance:** installation and audit pass against PostgreSQL, refusal behavior is demonstrated, and no current worktree or production resource changes.

## Task 5: Review and one complete regression

Review the whole branch for safety, hidden production defaults, missing model modules, secret leakage and documentation accuracy. Run the backend complete test suite once after the candidate is stable.

**Acceptance:** no critical review findings, complete suite green, `git diff --check` clean, no secrets or generated database dumps tracked.

## Task 6: Create and install the separate Cloud SQL instance

Create `cruise-v3-db-clean-20261006` in `cruise-v3-prod`, Tokyo, PostgreSQL 17, zonal `db-g1-small`, 10 GB SSD, backups/PITR and deletion protection enabled. Create a separate application database/user, install the schema with the verified tool and run the independent audit.

**Acceptance:** new instance is RUNNABLE, schema head is `0036_unified_data_tables`, all expected tables exist, the three system catalog rows exist, and every business table is empty. Existing `cruise-v3-db` metadata and application deployments remain unchanged.

## Task 7: Record handoff

**Files:** `docs/clean-database-bootstrap-2026-10-06/VERIFICATION.md`.

Record exact source commit, commands, instance configuration, audit results, isolation boundary, ongoing cost/deletion reminder and the intentionally deferred connection/migration/upload UI work.

**Acceptance:** the user can identify what was created, what was not changed, how to verify it and what decision comes next without consulting shell history.
