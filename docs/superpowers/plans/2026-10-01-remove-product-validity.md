# Remove Product Validity Implementation Plan

> **Execution:** implement inline with `superpowers:executing-plans`; every behavioral change follows RED → GREEN → refactor.

**Goal:** Remove product-level validity dates, safely migrate legacy values into purchase-price validity, and ensure price-period gaps create review warnings without breaking product matching.

**Architecture:** Canonical prices stay in `v3_product_price_periods`. A two-stage Alembic migration backfills safe legacy values and audits unsafe rows before the application stops using the old columns; a second migration physically drops the columns after production verification.

**Spec:** `docs/superpowers/specs/2026-10-01-remove-product-validity-design.md`

## Global constraints

- Never infer a missing or reversed date.
- Existing purchase-price fields and active canonical periods always win.
- Missing/unhit purchase price is a non-blocking row warning, never “product unmatched”.
- Preserve status, country and port candidate filtering.
- Old Excel headers fail explicitly instead of being ignored.
- Do not run a local full regression after every task: focused tests per task, connected-flow tests after integration, one local full regression before PR, one GitHub CI run.

## Task 1: Migration contracts and safe backfill

**Purpose:** Prove safe, loss-aware migration behavior before changing application code.

**Files:**
- Create `v3_backend/migrations/versions/0033_product_validity_backfill.py`
- Create `v3_backend/migrations/versions/0034_drop_product_validity.py`
- Modify `v3_backend/test_v2/section_1_security/test_session_security.py`
- Create `v3_backend/test_v2/section_6_masterdata/test_product_validity_migration.py`

**Steps:**
1. Write migration tests covering valid complete dates, single-ended dates, existing purchase values, existing exact periods, overlaps, amount conflicts, missing price, reversed dates and downgrade restoration.
2. Run the focused tests and confirm they fail because 0033/0034 do not exist.
3. Implement 0033 backfill/audit and 0034 drop/downgrade.
4. Run migration tests and migration-head guard.

**Acceptance:** no existing purchase data is overwritten; only valid complete rows create canonical periods; every touched row and every unsafe original value survives in `v3_product_validity_migration_audit`; upgrade to 0034 and downgrade to 0032 restore data correctly.

## Task 2: Matching independent from product validity

**Purpose:** Make product identity matching and price-period review two independent decisions.

**Files:**
- Modify `v3_backend/domains/orders/matching/code_first.py`
- Modify `v3_backend/domains/orders/matching/service.py`
- Modify `v3_backend/domains/orders/matching/automation.py`
- Modify `v3_backend/domains/orders/groups/matching.py`
- Modify `v3_backend/domains/inquiry/orchestrator.py`
- Modify `v3_backend/domains/orders/anomaly.py`
- Modify `v3_backend/test_v2/section_4_orders/test_product_matching.py`
- Modify `v3_backend/test_v2/section_6_masterdata/test_price_effective_periods.py`
- Modify `v3_backend/test_v2/section_9_web_api/test_arrangement_matching.py`
- Modify `v3_backend/test_v2/section_9_web_api/test_arrangement_inquiry_versions.py`
- Modify anomaly and automation-stage tests under `v3_backend/test_v2/section_4_orders/` and `section_e2e/`

**Steps:**
1. Add failing tests proving an enabled country/port product remains a match before/after any old date and when purchase periods are absent or unhit.
2. Add failing tests proving loading date selects the period and a missing loading date yields a review warning.
3. Remove product-date predicates and pass loading date explicitly through manual, arrangement and Oracle paths.
4. Change anomaly severity and inquiry generation so a matched row with a missing/unhit purchase period is non-actionable and included with a warning, then run the matching and inquiry-flow tests.

**Acceptance:** matched product/supplier IDs remain present; warning text is Chinese; actionable status is “可询价，需复核”; disabled products remain excluded.

## Task 3: Backend product contract cleanup

**Purpose:** Remove obsolete fields from runtime models and APIs while preserving status behavior.

**Files:**
- Modify `v3_backend/domains/masterdata/models.py`
- Modify `v3_backend/domains/masterdata/schemas.py`
- Modify `v3_backend/domains/masterdata/_products_service.py`
- Modify `v3_backend/domains/masterdata/repository.py`
- Modify product API/status tests under `v3_backend/test_v2/section_6_masterdata/` and `section_9_web_api/`
- Modify `v3_backend/domains/masterdata/images/bulk_service.py`
- Modify image option tests

**Steps:**
1. Write failing API/status/image ambiguity tests for the new contract.
2. Remove ORM/schema/service/repository references; define `is_effective` as status-only compatibility output where still consumed.
3. Replace image tie-breaking by old date with explicit ambiguity.
4. Run product CRUD, filter and image-option tests.

**Acceptance:** API requests/responses contain no old date fields; status filters behave unchanged; ambiguous image targets are never guessed.

## Task 4: Bulk upload, template and rollback correctness

**Purpose:** Make multi-period bulk pricing safe and usable after removal of old headers.

**Files:**
- Modify `v3_backend/domains/masterdata/upload/service.py`
- Modify `v3_backend/scripts/generate_product_upload_template.py`
- Regenerate `v3_backend/static/templates/product_upload_template.xlsx`
- Modify `v3_backend/agent/runtime/tools/data_upload.py`
- Modify upload stage/template/rollback/Agent tests

**Steps:**
1. Add failing tests for explicit rejection of old headers, same-range/different-amount conflicts, overlaps inside one workbook, overlaps with database periods and rollback removal of batch-created periods.
2. Implement shared period prevalidation before commit and transactional/compensating rollback behavior.
3. Remove old columns and regenerate the workbook.
4. Run all upload contract/stage/rollback/template tests.

**Acceptance:** valid duplicate product rows create multiple periods; every invalid row is reported before mutation; failed/rolled-back batches leave no period residue; template and Agent schemas agree.

## Task 5: Frontend cleanup

**Purpose:** Present only the surviving product concepts and the canonical price-period workflow.

**Files:**
- Modify `v3-frontend/src/lib/data-api.ts`
- Modify `v3-frontend/src/components/data/product-form-dialog.tsx`
- Modify `v3-frontend/src/components/data/product-basic-info.tsx`
- Modify `v3-frontend/src/app/dashboard/data/ProductsTab.tsx`
- Modify `v3-frontend/src/components/upload/UploadReviewCard.tsx`
- Modify affected Vitest fixtures/tests

**Steps:**
1. Update tests first to assert the old fields and labels are absent while status and price-period navigation remain.
2. Remove form, payload, list/filter, detail and review rendering references.
3. Run affected Vitest suites, TypeScript and production build.

**Acceptance:** no UI says “产品有效开始/结束日期”; product CRUD still works; purchase/selling price tabs and status controls remain intact.

## Task 6: Technical-debt sweep and connected-flow verification

**Purpose:** Ensure no runtime path or stale documentation still depends on removed columns.

**Files:**
- Update `PROGRESS.md` and relevant docs only after behavior is verified.
- Remove obsolete comments/helpers/tests discovered by `rg`.

**Steps:**
1. Search all runtime, migration, tests, templates and docs for generic product validity references; retain only historical migrations, the new audit migration and dated release history.
2. Run matching → anomaly → inquiry connected-flow tests and all upload tests.
3. Run one complete backend regression, one complete frontend regression, TypeScript and build.

**Acceptance:** runtime search is clean; all focused and full suites pass; generated artifacts are current.

## Task 7: Review, integration and production deployment

**Purpose:** Ship with recoverable database and application transitions.

**Scope:** Git history, GitHub CI, Cloud SQL, Cloud Run backend, Oracle Job, Vercel frontend, production verification documents.

**Steps:**
1. Review the whole branch against the spec; fix Important/Critical findings with RED → GREEN tests.
2. Commit, merge locally to main, push through PR/CI according to the repository release workflow.
3. Create a Cloud SQL backup; apply 0033 only and record row counts.
4. Deploy a 0%-traffic backend candidate, matching Oracle Job image and frontend preview; run health, API, matching and upload smoke checks.
5. Shift traffic/promote frontend, verify Oracle scheduled/manual scan, then back up again and apply 0034.
6. Verify columns absent, audit counts, product totals, representative PO matching and price warnings; update `PROGRESS.md` and a dated deployment record.

**Acceptance:** CI is green, production runs the recorded immutable revisions, DB head is 0034, old columns are absent, Oracle scanning continues, and user-facing smoke paths pass without writing speculative business data.

## Review focus

- A legacy row with one endpoint must never receive an invented endpoint.
- A reversed row must not break migration or disappear without audit evidence.
- Existing canonical periods must not be overwritten or duplicated.
- Oracle matching must not silently use delivery date as purchase-price date when loading date is absent.
- Price warnings must not inflate unmatched counts or block inquiry generation.
- Rollback must remove canonical periods created by the target upload batch.
- Downgrading 0034 must restore enough old columns for the prior production image to start safely.
