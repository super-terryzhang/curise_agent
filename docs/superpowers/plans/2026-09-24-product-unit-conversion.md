# Product Unit Conversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Execution choice:** The user selected native current-session execution. After plan approval, use `superpowers:executing-plans`; do not delegate implementation.

**Goal:** Build a reusable, audited unit-conversion registry that converts matched PO rows into supplier RFQ quantities and proves the behavior against the real `PO165047CCI` unit distribution.

**Architecture:** Add an expand-only `v3_unit_conversion_rules` table owned by masterdata, a Decimal-only evaluator exposed through `domains.masterdata.service`, and an orders coordinator that snapshots verified decisions into existing order and inquiry JSON. Existing one-row manual conversion remains the fallback; verified product rules take precedence over exact source-unit rules, and missing/stale/conflicting rules remain actionable anomalies.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL/SQLite tests, Pydantic v2, pytest; Next.js 16, React 19, TypeScript, Vitest, pnpm.

**Spec:** `docs/superpowers/specs/2026-09-24-product-unit-conversion-design.md`

## Global Constraints

- Preserve every PO row's original `quantity` and `unit`; conversion only adds `source_*`, `rfq_*`, and a structured evidence snapshot.
- Never infer break-pack permission and never round a fractional package quantity automatically.
- Use `Decimal` end to end; conversion decisions must not pass through binary floats.
- Do not backfill existing products, orders, inquiries, or prices in migration `0031_unit_conversion_rules`.
- Product rules require an exact `unit/unit_size/pack_size` fingerprint; source-unit rules require an exact source system, source unit, and target unit.
- `draft`, `retired`, expired, future, stale, and conflicting rules never auto-convert.
- One failing row must not stop Oracle scanning, matching, anomaly detection, or safe sibling rows.
- Production rollout is expand-first and reversible by `UNIT_CONVERSION_RULES_ENABLED=false`; old per-order manual evidence remains compatible.

## Review Focus

- Two verified rules for the same exact scope must be rejected rather than chosen by database/query order; Task 1 and Task 2 pin this with constraint and service tests.
- Decimal ratios that yield repeating or fractional package quantities must stay exact and raise `NON_INTEGER_PACKAGE_QUANTITY` when a known step is violated; Task 2 pins this.
- A product's `unit_size` or `pack_size` changing after approval must produce `UNIT_CONVERSION_RULE_STALE`; Task 2 and Task 3 pin this.
- Missing business dates must not make a dated rule match today's date; Task 2 pins no-date behavior.
- Existing verified one-row manual evidence and the disabled feature flag must preserve current behavior; Task 3 and Task 4 pin precedence and rollback.

---

### Task 1: Add the conversion-rule schema and ORM model

**Files:**
- Create: `v3_backend/migrations/versions/0031_unit_conversion_rules.py`
- Modify: `v3_backend/domains/masterdata/models.py`
- Modify: `v3_backend/domains/masterdata/__init__.py`
- Test: `v3_backend/test_v2/section_6_masterdata/test_unit_conversion_rules.py`

**Interfaces:**
- Produces: `UnitConversionRule` ORM model with table `v3_unit_conversion_rules`.
- Produces: database fields and constraints consumed by Task 2's service.

- [ ] **Step 1: Write failing model and constraint tests**

Add tests that construct a source-level rule and a product-level rule, then assert positive quantities, legal status, scope/product alignment, date ordering, verified actor/time requirements, and uniqueness of an active verified scope. The minimum factory shape is:

```python
def make_rule(**overrides):
    values = {
        "scope_type": "source_unit",
        "product_id": None,
        "source_system": "oracle",
        "source_unit": "KG2.2",
        "target_unit": "KG",
        "source_quantity": Decimal("1"),
        "target_quantity": Decimal("1"),
        "status": "draft",
        "evidence": "PO165047CCI exact unit and price comparison",
        "revision": 1,
        "created_by": 1,
        "updated_by": 1,
    }
    values.update(overrides)
    return UnitConversionRule(**values)
```

- [ ] **Step 2: Run the new tests and verify the model is missing**

Run: `cd v3_backend && python -m pytest test_v2/section_6_masterdata/test_unit_conversion_rules.py -q`

Expected: FAIL because `UnitConversionRule` is not importable.

- [ ] **Step 3: Add migration 0031 and the ORM model**

Create every field and constraint from the spec. Use `Numeric(20, 10)`, a nullable product FK with `ON DELETE CASCADE`, check constraints for status/scope/positive quantities/dates/verified metadata, and two partial unique indexes:

```python
postgresql_where=sa.text("status = 'verified' AND product_id IS NULL")
sqlite_where=sa.text("status = 'verified' AND product_id IS NULL")
```

and the corresponding product-scoped predicate `status = 'verified' AND product_id IS NOT NULL`. Export the model from `domains.masterdata.__init__` so matching code does not import another domain's private module.

- [ ] **Step 4: Run focused schema tests and architecture checks**

Run:

```bash
cd v3_backend
python -m pytest test_v2/section_6_masterdata/test_unit_conversion_rules.py test_v2/section_2_architecture/test_arch_check_rules.py -q
python scripts/check_arch.py
```

Expected: all tests pass and architecture reports zero violations.

- [ ] **Step 5: Commit the schema slice**

```bash
git add v3_backend/migrations/versions/0031_unit_conversion_rules.py v3_backend/domains/masterdata/models.py v3_backend/domains/masterdata/__init__.py v3_backend/test_v2/section_6_masterdata/test_unit_conversion_rules.py
git commit -m "feat: add audited unit conversion rules"
```

### Task 2: Implement rule CRUD, fingerprints, and Decimal evaluation

**Files:**
- Create: `v3_backend/domains/masterdata/_unit_conversion_service.py`
- Modify: `v3_backend/domains/masterdata/schemas.py`
- Modify: `v3_backend/domains/masterdata/service.py`
- Test: `v3_backend/test_v2/section_6_masterdata/test_unit_conversion_service.py`

**Interfaces:**
- Consumes: `UnitConversionRule` from Task 1.
- Produces: `normalize_unit(value: str) -> str` and `product_pack_signature(unit, unit_size, pack_size) -> str`.
- Produces: `evaluate_unit_conversion(db, *, product_id, source_system, source_quantity, source_unit, target_unit, business_date, product_unit, product_unit_size, product_pack_size, include_drafts=False) -> dict[str, Any]`.
- Produces: `create_unit_conversion_rule`, `verify_unit_conversion_rule`, `retire_unit_conversion_rule`, and `list_unit_conversion_rules` re-exported by `domains.masterdata.service`.

- [ ] **Step 1: Write failing evaluator tests**

Cover exact same-unit identity, `LB→KG` standard conversion, product-rule precedence, source-rule fallback, draft/expired/future/stale/conflicting rules, no-date behavior, non-positive input, known target steps, unknown break-pack state, and repeating ratios. Assert a success result has this stable contract:

```python
{
    "status": "converted",
    "source_quantity": Decimal("12"),
    "source_unit": "CA2.27",
    "target_quantity": Decimal("12"),
    "target_unit": "CT",
    "conversion_evidence": {
        "verified": True,
        "rule_id": rule.id,
        "rule_revision": 1,
        "scope_type": "product",
        "source_quantity": "1.0000000000",
        "target_quantity": "1.0000000000",
        "pack_signature": expected_signature,
        "evidence": "same supplier pack confirmed",
    },
}
```

- [ ] **Step 2: Run tests and verify service imports fail**

Run: `cd v3_backend && python -m pytest test_v2/section_6_masterdata/test_unit_conversion_service.py -q`

Expected: FAIL because evaluator and schemas do not exist.

- [ ] **Step 3: Implement schemas and focused service**

Use Pydantic requests `UnitConversionRuleCreate`, `UnitConversionRuleVerify`, and `UnitConversionRuleRetire`. Keep all SQL and model access inside `_unit_conversion_service.py`; `service.py` only re-exports public functions. Normalize Unicode width, trim whitespace, and uppercase units without altering the order's raw strings. The formula must be:

```python
target = source * rule.target_quantity / rule.source_quantity
```

Use an explicit standard-unit map for mass and volume; do not parse arbitrary package text as authority. Return one of `same`, `converted`, or `review` plus a stable issue code for review results.

- [ ] **Step 4: Implement rule lifecycle and conflict handling**

Creating a rule produces `draft` unless an admin explicitly verifies it. Verification requires evidence, actor id, current revision, and exact product fingerprint when product-scoped. Convert database integrity conflicts into the existing masterdata `Conflict` error; never retry by silently retiring another rule.

- [ ] **Step 5: Run focused and masterdata regression tests**

Run:

```bash
cd v3_backend
python -m pytest test_v2/section_6_masterdata/test_unit_conversion_rules.py test_v2/section_6_masterdata/test_unit_conversion_service.py test_v2/section_6_masterdata/test_crud_service.py -q
python scripts/check_arch.py
```

Expected: all pass.

- [ ] **Step 6: Commit the service slice**

```bash
git add v3_backend/domains/masterdata/_unit_conversion_service.py v3_backend/domains/masterdata/schemas.py v3_backend/domains/masterdata/service.py v3_backend/test_v2/section_6_masterdata/test_unit_conversion_service.py
git commit -m "feat: evaluate reusable unit conversions"
```

### Task 3: Apply verified rules during matching and inquiry preparation

**Files:**
- Create: `v3_backend/domains/orders/matching/unit_conversion.py`
- Modify: `v3_backend/domains/orders/matching/service.py`
- Modify: `v3_backend/domains/orders/anomaly.py`
- Modify: `v3_backend/domains/orders/issues.py`
- Modify: `v3_backend/domains/inquiry/automation.py`
- Modify: `v3_backend/infrastructure/config.py`
- Test: `v3_backend/test_v2/section_4_orders/test_order_unit_conversion.py`
- Test: `v3_backend/test_v2/section_5_inquiry/test_automated_unit_conversion.py`

**Interfaces:**
- Consumes: `masterdata.service.evaluate_unit_conversion` from Task 2.
- Produces: `apply_verified_unit_conversions(order, db, results, business_date) -> None`.
- Produces: structured conversion results already understood by existing anomaly and inquiry code.

- [ ] **Step 1: Write failing matching and inquiry tests**

Pin the following behavior: feature disabled preserves today's warning; manual row evidence wins; verified rule adds `rfq_quantity/rfq_unit/conversion_evidence`; stale/conflicting rules emit their specific codes; an exception in one row becomes a review finding while the next row converts; inquiry preparation trusts a verified rule snapshot and no longer requires a source-file-specific `unit_approvals` entry.

- [ ] **Step 2: Run tests and verify the integration is absent**

Run:

```bash
cd v3_backend
python -m pytest test_v2/section_4_orders/test_order_unit_conversion.py test_v2/section_5_inquiry/test_automated_unit_conversion.py -q
```

Expected: FAIL with unit-conversion warnings still present.

- [ ] **Step 3: Add the feature flag and matching coordinator**

Add `UNIT_CONVERSION_RULES_ENABLED: bool = False` to `Settings`. After exact/fuzzy matching, iterate only matched rows, preserve explicit manual evidence, call the public masterdata evaluator, and attach successful snapshots. On `review`, attach `unit_conversion_issue` with code/message/evidence; catch unexpected exceptions per row and use `UNIT_CONVERSION_EVALUATION_FAILED` without aborting matching.

- [ ] **Step 4: Teach anomaly and inquiry flows the new result contract**

Map `UNIT_CONVERSION_RULE_STALE`, `UNIT_CONVERSION_RULE_CONFLICT`, `NON_INTEGER_PACKAGE_QUANTITY`, and `UNIT_CONVERSION_EVALUATION_FAILED` to dynamic issue actions. In `prepare_inquiry`, use `rfq_quantity/rfq_unit` only when `conversion_evidence.verified is True`; otherwise retain the existing external approval and exclusion behavior for backward compatibility.

- [ ] **Step 5: Run integration and existing inquiry regressions**

Run:

```bash
cd v3_backend
python -m pytest test_v2/section_4_orders/test_order_unit_conversion.py test_v2/section_4_orders/test_order_issue_overview.py test_v2/section_5_inquiry/test_automated_unit_conversion.py test_v2/section_5_inquiry/test_automated_workbook_quality.py test_v2/section_5_inquiry/test_template_engine.py -q
python scripts/check_arch.py
```

Expected: all pass.

- [ ] **Step 6: Commit the matching slice**

```bash
git add v3_backend/domains/orders/matching/unit_conversion.py v3_backend/domains/orders/matching/service.py v3_backend/domains/orders/anomaly.py v3_backend/domains/orders/issues.py v3_backend/domains/inquiry/automation.py v3_backend/infrastructure/config.py v3_backend/test_v2/section_4_orders/test_order_unit_conversion.py v3_backend/test_v2/section_5_inquiry/test_automated_unit_conversion.py
git commit -m "feat: apply verified conversions to matched orders"
```

### Task 4: Add audited rule APIs and reusable decisions to the row workflow

**Files:**
- Modify: `v3_backend/apps/http/masterdata.py`
- Modify: `v3_backend/domains/orders/schemas.py`
- Modify: `v3_backend/domains/orders/service.py`
- Test: `v3_backend/test_v2/section_9_web_api/test_unit_conversion_rules_api.py`
- Test: `v3_backend/test_v2/section_9_web_api/test_orders_api.py`

**Interfaces:**
- Consumes: lifecycle functions from Task 2 and matching integration from Task 3.
- Produces: `GET/POST /api/data/unit-conversion-rules`, `PATCH /api/data/unit-conversion-rules/{id}/verify`, and `PATCH /api/data/unit-conversion-rules/{id}/retire`.
- Extends: `OrderRowResolveRequest.conversion_scope` with `order_row | product | source_unit` and optional reusable rule-basis fields.

- [ ] **Step 1: Write failing authorization and round-trip API tests**

Assert writers can still save `order_row`; only admin/superadmin can create, verify, or retire reusable rules; product scope captures the current matched product and fingerprint; source-unit scope has no product id and matches exact units only; stale revisions return 409; invalid quantities/evidence/scope return Chinese 400 messages.

- [ ] **Step 2: Run the endpoint tests and verify the routes are missing**

Run:

```bash
cd v3_backend
python -m pytest test_v2/section_9_web_api/test_unit_conversion_rules_api.py test_v2/section_9_web_api/test_orders_api.py -q
```

Expected: FAIL with 404 or missing request fields.

- [ ] **Step 3: Add thin Admin-gated masterdata endpoints**

Use the existing `Admin` dependency and `_translate` error mapping. Keep writes in domain services. List access can use `Writer`; mutation endpoints must use `Admin`, record `_admin.id`, and require `expected_revision` for verify/retire.

- [ ] **Step 4: Extend row resolution without weakening the current fallback**

For `order_row`, preserve today's behavior. For reusable scopes, require `rule_source_quantity` and `rule_target_quantity`, verify that applying the proposed basis exactly reproduces the entered `rfq_quantity`, and reject fractional-step inconsistencies. `product` captures the matched product fingerprint; `source_unit` is admin-only and exact-pair scoped. After persistence, rerun matching and anomalies atomically.

- [ ] **Step 5: Run API, permission, and rollback tests**

Run:

```bash
cd v3_backend
python -m pytest test_v2/section_9_web_api/test_unit_conversion_rules_api.py test_v2/section_9_web_api/test_orders_api.py test_v2/section_1_security/test_user_capabilities.py -q
```

Expected: all pass; employee reusable-scope attempts are rejected without leaving rules or row evidence.

- [ ] **Step 6: Commit the API slice**

```bash
git add v3_backend/apps/http/masterdata.py v3_backend/domains/orders/schemas.py v3_backend/domains/orders/service.py v3_backend/test_v2/section_9_web_api/test_unit_conversion_rules_api.py v3_backend/test_v2/section_9_web_api/test_orders_api.py
git commit -m "feat: manage and reuse conversion decisions"
```

### Task 5: Update the order dialog for explicit reuse scopes

**Files:**
- Create: `v3-frontend/src/lib/unit-conversion-view.ts`
- Create: `v3-frontend/src/lib/unit-conversion-view.test.ts`
- Modify: `v3-frontend/src/lib/orders-api.ts`
- Modify: `v3-frontend/src/components/orders/order-row-resolution-dialog.tsx`

**Interfaces:**
- Consumes: extended row-resolution payload from Task 4.
- Produces: a three-scope conversion form with admin-only reusable scopes and explicit rule basis.

- [ ] **Step 1: Write failing pure view-model tests**

Test that employee/finance see only `order_row`; admin/superadmin additionally see `product` and `source_unit`; product scope labels include the current product and pack; source-unit labels display the exact pair and never a wildcard. Test payload construction requires reusable basis and evidence.

- [ ] **Step 2: Run Vitest and verify helpers are absent**

Run: `cd v3-frontend && pnpm test -- src/lib/unit-conversion-view.test.ts`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement typed API fields and view helpers**

Add `conversion_scope`, `rule_source_quantity`, `rule_target_quantity`, `target_step`, and `break_pack` to `OrderRowResolveRequest`. Export pure helpers for scope visibility, labels, and request construction so behavior is testable without a browser DOM library.

- [ ] **Step 4: Revise the existing dialog**

Keep the default `order_row`. For reusable scopes, show a compact relation editor such as `1 CA2.27 = 1 CT`, current product/pack evidence, target step, tri-state break-pack status, and a warning describing the exact reuse scope. Never preselect source-wide reuse. Use `getUser()` only for UI visibility; backend authorization remains authoritative.

- [ ] **Step 5: Run frontend tests, type checking, and production build**

Run:

```bash
cd v3-frontend
pnpm test
pnpm exec tsc --noEmit
pnpm build
```

Expected: all tests pass, TypeScript has no errors, and Next production build succeeds.

- [ ] **Step 6: Commit the frontend slice**

```bash
git add v3-frontend/src/lib/unit-conversion-view.ts v3-frontend/src/lib/unit-conversion-view.test.ts v3-frontend/src/lib/orders-api.ts v3-frontend/src/components/orders/order-row-resolution-dialog.tsx
git commit -m "feat: choose reusable unit conversion scope"
```

### Task 6: Lock PO165047CCI into a deterministic regression and shadow audit

**Files:**
- Create: `v3_backend/test_v2/section_4_orders/fixtures/po165047cci_units.json`
- Create: `v3_backend/test_v2/section_4_orders/test_po165047cci_unit_conversion.py`
- Create: `v3_backend/scripts/audit_unit_conversions.py`
- Test: `v3_backend/test_v2/section_4_orders/test_po165047cci_unit_conversion.py`

**Interfaces:**
- Consumes: rules/evaluator/matching from Tasks 1–4.
- Produces: anonymized 56-row fixture and a read-only audit command with `--po-number` and `--include-drafts`.

- [ ] **Step 1: Add the anonymized 56-row fixture and failing acceptance test**

The fixture contains only row number, synthetic product code, source quantity/unit, match status, target unit, pack snapshot, and a marker for the single product-specific row. Assert exact counts: 53 matched / 3 not matched; 46 `KG2.2→KG`; 4 `CA22.0→CA`; one each for the other three matched pairs.

- [ ] **Step 2: Test both verified and safe-incomplete states**

With all five exact rules verified, assert 53 unit conversions and product-row 12 equals `12 CT`. Retire the product-specific rule and rerun: assert 52 unit conversions, one `UNIT_CONVERSION_REQUIRED`, and the same 3 `PRODUCT_NOT_MATCHED` rows. Assert no original source quantity/unit changed.

- [ ] **Step 3: Run the PO acceptance test**

Run: `cd v3_backend && python -m pytest test_v2/section_4_orders/test_po165047cci_unit_conversion.py -q`

Expected: PASS after Tasks 1–4 are complete.

- [ ] **Step 4: Add a transaction-read-only audit command**

`audit_unit_conversions.py` must issue `SET TRANSACTION READ ONLY`, load one PO, evaluate verified rules or drafts in shadow mode, roll back explicitly, and print JSON counts without writing orders or rules. Exit nonzero only for infrastructure/query failure; unresolved business rows remain in the JSON result.

- [ ] **Step 5: Run the audit against a local fixture database and code-quality gates**

Run:

```bash
cd v3_backend
python -m pytest test_v2/section_4_orders/test_po165047cci_unit_conversion.py -q
python -m ruff check domains/masterdata/_unit_conversion_service.py domains/orders/matching/unit_conversion.py scripts/audit_unit_conversions.py
python scripts/check_arch.py
```

Expected: all pass.

- [ ] **Step 6: Commit the acceptance slice**

```bash
git add v3_backend/test_v2/section_4_orders/fixtures/po165047cci_units.json v3_backend/test_v2/section_4_orders/test_po165047cci_unit_conversion.py v3_backend/scripts/audit_unit_conversions.py
git commit -m "test: lock PO165047CCI conversion behavior"
```

### Task 7: Consolidate, review, run full verification, and prepare release evidence

**Files:**
- Create: `docs/unit-conversion-2026-09-24/PROGRESS.md`
- Create: `docs/unit-conversion-2026-09-24/REVIEW.md`
- Modify outside this repository after deployment status is known: `/Users/yichuanzhang/Desktop/curise_system_2/PROGRESS.md`
- Create outside this repository only after production evidence exists: `/Users/yichuanzhang/Desktop/curise_system_2/DEPLOYMENT_VERIFIED_2026-09-24_UNIT_CONVERSION.md`

**Interfaces:**
- Consumes: all implementation slices.
- Produces: review evidence, release boundary, rollback steps, and user-check instructions.

- [ ] **Step 1: Review the entire diff against the spec**

Check for duplicate conversion logic, float coercion, direct cross-domain model imports, guessed break-pack behavior, stale manual evidence, permissive scope matching, hidden row exceptions, and obsolete comments in `inquiry/automation.py`. Record every finding and resolution in `REVIEW.md`.

- [ ] **Step 2: Run the complete local verification matrix**

Run:

```bash
cd v3_backend
python -m pytest -q
python scripts/check_arch.py
python -m ruff check migrations/versions/0031_unit_conversion_rules.py domains/masterdata/_unit_conversion_service.py domains/orders/matching/unit_conversion.py apps/http/masterdata.py domains/orders/service.py scripts/audit_unit_conversions.py
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir /tmp/cruise-unit-wheel
cd ../v3-frontend
pnpm test
pnpm exec tsc --noEmit
pnpm build
```

Expected: zero failures; existing documented warnings may remain but no new warnings are accepted without review.

- [ ] **Step 3: Rehearse migration and rollback outside production**

Run migration `0030→0031`, inspect columns/constraints/indexes, run `0031→0030`, then `0030→0031` again against a disposable PostgreSQL database. Verify counts and hashes for products, orders, inquiries, and prices do not change; only the empty rule table is added.

- [ ] **Step 4: Commit consolidation and request whole-branch review**

```bash
git add docs/unit-conversion-2026-09-24/PROGRESS.md docs/unit-conversion-2026-09-24/REVIEW.md
git commit -m "docs: record unit conversion verification"
```

Use `superpowers:requesting-code-review`; fix every confirmed correctness issue and rerun the impacted tests before proceeding.

- [ ] **Step 5: Push, open the PR, and require green CI**

Push `design/product-unit-conversion-20260924`, open a PR to `main`, and require backend/frontend jobs to pass. Merge without force-push; record the merge commit and CI run in `docs/unit-conversion-2026-09-24/PROGRESS.md`.

- [ ] **Step 6: Execute expand-first production rollout**

Create a fresh on-demand Cloud SQL backup, build an immutable image from the exact merge commit, and prepare a one-shot Cloud Run migration Job for `0031_unit_conversion_rules`. Execute migration before deploying the new backend because startup enforces the current Alembic head; verify head `0031_unit_conversion_rules`, table constraints, and unchanged business counts before sending traffic to a 0% candidate revision.

- [ ] **Step 7: Canary backend, frontend, Oracle Job, and feature flag**

Verify candidate `/health`, OpenAPI routes, unauthenticated 401, Admin/Writer authorization, CORS, and the read-only audit with the feature disabled. Enable `UNIT_CONVERSION_RULES_ENABLED=true`, rerun the audit, then switch backend traffic, deploy/promote the frontend, and update the hourly Oracle Job to the identical image digest. Keep all schedulers enabled unless a migration window requires a documented temporary pause.

- [ ] **Step 8: Create drafts, not guessed verified facts, for PO165047CCI**

Create the five exact candidates as `draft` through the service/API, then run the read-only audit with and without drafts. Expected shadow result is 53 convertible candidates and 3 unmatched rows; automatic production result remains unchanged until an administrator explicitly verifies each rule. Do not mark `CA2.27→CT` verified solely from `86gX12` text.

- [ ] **Step 9: Record production evidence and hand off user review**

Write `/Users/yichuanzhang/Desktop/curise_system_2/DEPLOYMENT_VERIFIED_2026-09-24_UNIT_CONVERSION.md` with database head, backup id, migration execution, backend revision/digest, frontend deployment, Oracle Job generation, CI run, rule counts by status, PO165047CCI audit counts, rollback path, and known debt. Update `/Users/yichuanzhang/Desktop/curise_system_2/PROGRESS.md` without rewriting older dated records, then ask the user to verify the order dialog, rule scopes, one draft approval, the rerun result, and the generated inquiry Excel.
