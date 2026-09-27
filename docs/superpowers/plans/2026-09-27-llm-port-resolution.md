# LLM Port Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a constrained LLM select a valid database port for Oracle POs without blocking downstream automation, while exposing every AI decision for later human confirmation or override.

**Architecture:** A new `domains/orders/port_resolution` package owns the typed Gemini call, candidate validation, persistence state transitions, and feature-flag fallback. Oracle import invokes it before product matching; HTTP endpoints confirm or override a decision; anomaly and frontend projections expose the non-blocking review state without inflating product-row issue counts.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy/Alembic, google-genai structured output, pytest, Next.js 16, React 19, TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-27-llm-port-resolution-design.md`

## Global Constraints

- A valid LLM port decision must not block product matching, grouping, inquiry generation, or anomaly detection.
- The LLM may return only a listed `port_id` or `unmatched`; application code ignores any model-generated display name.
- The LLM sees verified `FINAL DESTINATION` text only; Oracle `PORT CODE` is retained as evidence but is not interpreted by the model.
- Unknown, ambiguous, invalid, or unavailable model results isolate the current PO and never stop the Oracle scan batch.
- `LLM_PORT_REVIEW_REQUIRED` is an order-level non-blocking warning and must not increase product-row actionable counts.
- Confirming the current AI port is idempotent and must not create a new inquiry version; overriding it reruns stages 5–8 exactly once.
- Old orders remain `NULL` for the new resolution fields until a new resolution or manual edit occurs.
- Do not modify product-unit conversion, price periods, PO line extraction, inquiry Excel layout, outbound messaging, or existing port master-data names/codes.

## Review Focus

- A stale confirmation tab submits an old `decision_id`: return `409`, preserve the newer decision, and do not rerun anything (Task 5 test).
- The LLM returns `matched` with a boolean, string, or out-of-candidate ID, or the selected port becomes disabled/country-less before application: reject it as `LLM_PORT_RESOLUTION_FAILED` (Tasks 2–3 tests).
- A destination contains prompt-like instructions or oversized text: treat it as data, enforce a length bound, and either match a candidate or return unresolved without tool execution (Task 2 tests).
- A user overrides the AI port after an inquiry already exists: preserve the old inquiry, move/rematch the PO, and create only one correct new version (Task 5 integration test).
- Multiple Oracle POs run in one scan while one LLM call times out: the failed PO enters review and the following PO completes (Task 4 batch-isolation test).

---

## File Structure

### New backend files

- `v3_backend/migrations/versions/0032_llm_port_resolution.py` — additive order columns and check constraints.
- `v3_backend/domains/orders/port_resolution/__init__.py` — narrow public interface.
- `v3_backend/domains/orders/port_resolution/types.py` — typed candidate, decision, and persisted-state contracts.
- `v3_backend/domains/orders/port_resolution/resolver.py` — native Gemini structured call and strict response validation; no writes.
- `v3_backend/domains/orders/port_resolution/service.py` — state transitions, row locking, canonical port lookup, and idempotency.
- `v3_backend/test_v2/section_4_orders/test_port_resolution.py` — unit/service tests.
- `v3_backend/test_v2/section_e2e/test_llm_port_resolution_eval.py` — opt-in real-model evaluation.

### Modified backend files

- `v3_backend/infrastructure/config.py` — feature flag and explicit resolver model.
- `v3_backend/domains/orders/models.py` — resolution persistence fields.
- `v3_backend/domains/orders/schemas.py` — API DTOs and `OrderDetail`/list projections.
- `v3_backend/domains/orders/matching/automation.py` — consume an already validated port instead of hard-coded translation.
- `v3_backend/apps/jobs/oracle_po.py` — resolve port, continue on AI match, isolate unresolved/failure.
- `v3_backend/domains/orders/anomaly.py` — AI review findings and not-run-vs-unmatched correction.
- `v3_backend/domains/orders/service.py` — confirm/override orchestration and API serialization.
- `v3_backend/apps/http/orders.py` — two authenticated endpoints.
- `v3_backend/domains/orders/groups/arrangements.py` — propagate resolution summaries and route arrangement port edits through manual override semantics.
- `v3_backend/test_v2/section_4_orders/test_oracle_import.py` — Oracle pipeline and batch behavior.
- `v3_backend/test_v2/section_9_web_api/test_orders_api.py` — HTTP confirmation/override contracts.
- `v3_backend/test_v2/section_9_web_api/test_arrangements.py` — arrangement warning summary and override behavior.
- `v3_backend/test_v2/section_1_security/test_session_security.py` — expected migration head.

### New frontend files

- `v3-frontend/src/lib/port-resolution-view.ts` — label/tone/action derivation.
- `v3-frontend/src/lib/port-resolution-view.test.ts` — deterministic UI-state tests.
- `v3-frontend/src/components/orders/PortResolutionReview.tsx` — compact confirm/override panel.

### Modified frontend files

- `v3-frontend/src/lib/orders-api.ts` — resolution types and confirm/override calls.
- `v3-frontend/src/lib/order-groups-api.ts` — arrangement-member resolution summaries.
- `v3-frontend/src/app/dashboard/orders/[id]/page.tsx` — badge, evidence, confirmation and override UI.
- `v3-frontend/src/components/orders/OrderGroupsSection.tsx` — PO-row badge.
- `v3-frontend/src/app/dashboard/orders/arrangements/[id]/page.tsx` — arrangement/inquiry warning banner.
- Existing view tests where fixtures require the new optional shape.

## Task 1: Persistence and API Read Contracts

**Files:**
- Create: `v3_backend/migrations/versions/0032_llm_port_resolution.py`
- Modify: `v3_backend/domains/orders/models.py`
- Modify: `v3_backend/domains/orders/schemas.py`
- Modify: `v3_backend/domains/orders/service.py`
- Modify: `v3_backend/infrastructure/config.py`
- Modify: `v3_backend/test_v2/section_1_security/test_session_security.py`
- Test: `v3_backend/test_v2/section_9_web_api/test_orders_api.py`

**Interfaces:**
- Produces: `PortResolutionState`, `PortResolutionConfirmRequest`, `PortResolutionOverrideRequest` DTOs; `Order.port_resolution_*` columns; `OrderDetail.port_resolution` and `OrderListItem.port_resolution`.
- Produces config: `LLM_PORT_RESOLUTION_ENABLED: bool = False`, `LLM_PORT_RESOLUTION_MODEL: str = "gemini-3.5-flash"`, `LLM_PORT_RESOLUTION_TIMEOUT_MS: int = 15000`, and `LLM_PORT_RESOLUTION_ATTEMPTS: int = 2`.

- [ ] **Step 1: Add failing API projection tests**

Add `test_order_detail_exposes_pending_llm_port_resolution` and `test_order_list_exposes_pending_llm_port_resolution`. Seed raw persistence fields and assert the typed payload includes method, status, source text, selected/final IDs, model, prompt version, decision ID, reason and timestamps; legacy orders return `null`.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `cd v3_backend && pytest test_v2/section_9_web_api/test_orders_api.py -k port_resolution -v`

Expected: FAIL because the ORM and DTO fields do not exist.

- [ ] **Step 3: Add migration, ORM fields, DTOs and serializers**

Migration revision `0032_llm_port_resolution` revises `0031_unit_conversion_rules`. Add nullable method/status/data/reviewer/reviewed-at columns, FK reviewer to `users.id`, and check constraints for `llm|manual` and `pending_review|confirmed|overridden|unresolved`. Add config values and read projections without backfilling legacy rows.

- [ ] **Step 4: Update migration-head guard and run focused tests**

Run: `cd v3_backend && pytest test_v2/section_1_security/test_session_security.py test_v2/section_9_web_api/test_orders_api.py -k 'migration or port_resolution' -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v3_backend/migrations/versions/0032_llm_port_resolution.py v3_backend/infrastructure/config.py v3_backend/domains/orders/models.py v3_backend/domains/orders/schemas.py v3_backend/domains/orders/service.py v3_backend/test_v2/section_1_security/test_session_security.py v3_backend/test_v2/section_9_web_api/test_orders_api.py
git commit -m "feat: add port resolution persistence contract"
```

## Task 2: Constrained Gemini Port Resolver

**Files:**
- Create: `v3_backend/domains/orders/port_resolution/__init__.py`
- Create: `v3_backend/domains/orders/port_resolution/types.py`
- Create: `v3_backend/domains/orders/port_resolution/resolver.py`
- Create: `v3_backend/test_v2/section_4_orders/test_port_resolution.py`

**Interfaces:**
- Consumes: resolver model/feature settings from Task 1.
- Produces: `PortCandidate(id: int, name: str, country_id: int)`, `PortResolutionDecision(status, port_id, reason, model, prompt_version, candidate_snapshot_hash)`, constants `MAX_DESTINATION_CHARS = 200` / `MAX_REASON_CHARS = 500`, and `resolve_destination(destination: str, candidates: Sequence[PortCandidate], *, api_key: str, model: str, timeout_ms: int, attempts: int) -> PortResolutionDecision`.

- [ ] **Step 1: Write failing resolver contract tests**

Mock the native Gemini client and cover: valid `matched`; valid `unmatched`; empty candidate list avoids a model call; string/boolean/out-of-list IDs rejected; malformed JSON rejected without retry; reasons over 500 characters rejected; one transient 429/5xx/timeout is retried and can succeed; a second transient failure stops after exactly two attempts; destination text containing instructions remains a quoted data field; destination over 200 characters returns an unresolved validation result without a model call.

- [ ] **Step 2: Run the resolver tests and verify failure**

Run: `cd v3_backend && pytest test_v2/section_4_orders/test_port_resolution.py -v`

Expected: FAIL on missing package/interfaces.

- [ ] **Step 3: Implement the typed resolver**

Use `google.genai.Client.models.generate_content` with `response_mime_type="application/json"`, a strict schema containing only `status`, nullable integer `port_id`, and `reason`, `temperature=0`, prompt version `oracle-port-resolution-v1`, and native `HttpOptions(timeout=15000, retry_options=HttpRetryOptions(attempts=2, ...))`. Retry only transient HTTP/provider failures, keep the total attempt count bounded, validate the parsed result independently of the response schema, and never expose model-generated names.

- [ ] **Step 4: Run resolver tests and Ruff**

Run: `cd v3_backend && pytest test_v2/section_4_orders/test_port_resolution.py -v && ruff check domains/orders/port_resolution test_v2/section_4_orders/test_port_resolution.py`

Expected: PASS and no lint violations.

- [ ] **Step 5: Commit**

```bash
git add v3_backend/domains/orders/port_resolution v3_backend/test_v2/section_4_orders/test_port_resolution.py
git commit -m "feat: add constrained llm port resolver"
```

## Task 3: Resolution State Service and Anomaly Semantics

**Files:**
- Modify: `v3_backend/domains/orders/port_resolution/service.py`
- Modify: `v3_backend/domains/orders/anomaly.py`
- Test: `v3_backend/test_v2/section_4_orders/test_port_resolution.py`
- Test: `v3_backend/test_v2/section_4_orders/test_order_issue_overview.py`

**Interfaces:**
- Consumes: `PortResolutionDecision` from Task 2 and persistence fields from Task 1.
- Produces: `resolve_order_port(db, order, *, destination, source_code, api_key, model) -> PortResolutionOutcome`; `confirm_order_port(...)`; `override_order_port(...)`; anomaly findings keyed by the persisted state.

- [ ] **Step 1: Write failing state and anomaly tests**

Assert: matched decisions revalidate the current database row, apply canonical port/country and create `llm + pending_review`; a selected port disabled or stripped of its country after the model response becomes `LLM_PORT_RESOLUTION_FAILED`; unmatched/provider failure clears only an unconfirmed automatic port and records exact failure; pending state emits one non-blocking `LLM_PORT_REVIEW_REQUIRED`; unresolved and failure emit their error codes; `match_results=None` does not emit `PRODUCT_NOT_MATCHED`; an explicit `not_matched` row still does.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd v3_backend && pytest test_v2/section_4_orders/test_port_resolution.py test_v2/section_4_orders/test_order_issue_overview.py -v`

Expected: FAIL on missing service and current false-positive behavior.

- [ ] **Step 3: Implement service state transitions and anomaly rules**

Persist UUID decision IDs and UTC decision timestamps. `resolve_order_port` catches model/provider/validation exceptions and returns an outcome code instead of raising into the batch. Update `ROW_MATCH_AND_PRICE` to skip row matching findings when `match_results is None`; add a dedicated order-level rule for resolution status.

- [ ] **Step 4: Run focused tests**

Run: `cd v3_backend && pytest test_v2/section_4_orders/test_port_resolution.py test_v2/section_4_orders/test_order_issue_overview.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v3_backend/domains/orders/port_resolution/service.py v3_backend/domains/orders/anomaly.py v3_backend/test_v2/section_4_orders/test_port_resolution.py v3_backend/test_v2/section_4_orders/test_order_issue_overview.py
git commit -m "feat: persist llm port review state"
```

## Task 4: Oracle Import Integration and Batch Isolation

**Files:**
- Modify: `v3_backend/domains/orders/matching/automation.py`
- Modify: `v3_backend/apps/jobs/oracle_po.py`
- Modify: `v3_backend/apps/jobs/oracle_scan.py`
- Test: `v3_backend/test_v2/section_4_orders/test_oracle_import.py`
- Test: `v3_backend/test_v2/section_4_orders/test_oracle_scan.py`

**Interfaces:**
- Consumes: `resolve_order_port` from Task 3.
- Produces: Oracle issues `LLM_PORT_UNRESOLVED` / `LLM_PORT_RESOLUTION_FAILED`; valid LLM decisions proceed through matching, grouping, inquiry and anomaly stages with a warning.

- [ ] **Step 1: Replace the Tokyo fixture assumption with failing LLM-path tests**

Add tests for `OSAKA -> port 21 -> exact product -> group -> inquiry -> pending review`; unknown destination unresolved without inquiry; timeout isolated to the PO; next PO in the same scan still completes; feature flag off preserves the current manual-review fallback. Mock the resolver boundary, not `google.genai`.

- [ ] **Step 2: Run Oracle tests and verify failure**

Run: `cd v3_backend && pytest test_v2/section_4_orders/test_oracle_import.py test_v2/section_4_orders/test_oracle_scan.py -v`

Expected: FAIL because Oracle import still uses the hard-coded Tokyo resolver.

- [ ] **Step 3: Integrate the resolution service**

Move canonical port selection out of `match_for_import`; make it require an already validated `order.port_id/country_id` and only load the product pool/match rows. In `import_po`, resolve port before matching when the flag is on, preserve the existing safe review path when off, and keep source issue/result serialization stable.

- [ ] **Step 4: Verify Oracle import and batch isolation**

Run: `cd v3_backend && pytest test_v2/section_4_orders/test_oracle_import.py test_v2/section_4_orders/test_oracle_scan.py -v`

Expected: PASS, including the following-PO-after-timeout assertion.

- [ ] **Step 5: Commit**

```bash
git add v3_backend/domains/orders/matching/automation.py v3_backend/apps/jobs/oracle_po.py v3_backend/apps/jobs/oracle_scan.py v3_backend/test_v2/section_4_orders/test_oracle_import.py v3_backend/test_v2/section_4_orders/test_oracle_scan.py
git commit -m "feat: resolve oracle ports with llm fallback"
```

## Task 5: Human Confirm and Override APIs

**Files:**
- Modify: `v3_backend/domains/orders/port_resolution/service.py`
- Modify: `v3_backend/domains/orders/service.py`
- Modify: `v3_backend/domains/orders/schemas.py`
- Modify: `v3_backend/apps/http/orders.py`
- Test: `v3_backend/test_v2/section_9_web_api/test_orders_api.py`

**Interfaces:**
- Consumes: persisted resolution state and existing matching/group/inquiry services.
- Produces: `POST /api/orders/{id}/port-resolution/confirm` and `/override`; both return `OrderDetail`.

- [ ] **Step 1: Write failing HTTP/service tests**

Cover owner/admin authorization; confirm same decision; repeat confirm idempotency; stale `decision_id` returns 409; override validates active country-backed port; repeat override idempotency; override after an inquiry preserves the old version and creates exactly one new correct version; manual edit of a pending AI port routes through override semantics.

- [ ] **Step 2: Run endpoint tests and verify failure**

Run: `cd v3_backend && pytest test_v2/section_9_web_api/test_orders_api.py -k 'port_resolution or override_ai_port' -v`

Expected: FAIL with missing routes.

- [ ] **Step 3: Implement atomic endpoints**

Use `SELECT ... FOR UPDATE` in service methods. Confirm changes only review fields and reruns anomaly detection. Override validates decision/port, preserves LLM evidence, updates canonical port/country, and invokes a single stages-5–8 continuation with an idempotency guard stored against the decision/final port pair.

- [ ] **Step 4: Run endpoint and inquiry-version regression tests**

Run: `cd v3_backend && pytest test_v2/section_9_web_api/test_orders_api.py test_v2/section_9_web_api/test_arrangement_inquiry_versions.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v3_backend/domains/orders/port_resolution/service.py v3_backend/domains/orders/service.py v3_backend/domains/orders/schemas.py v3_backend/apps/http/orders.py v3_backend/test_v2/section_9_web_api/test_orders_api.py
git commit -m "feat: add llm port review actions"
```

## Task 6: Arrangement Projections and Manual Arrangement Edits

**Files:**
- Modify: `v3_backend/domains/orders/groups/arrangements.py`
- Modify: `v3_backend/domains/orders/groups/schemas.py`
- Test: `v3_backend/test_v2/section_9_web_api/test_arrangements.py`

**Interfaces:**
- Consumes: `Order.port_resolution_*`.
- Produces: member-level `port_resolution` and summary `pending_port_review_count`; arrangement port edits mark affected AI decisions overridden through the shared service semantics.

- [ ] **Step 1: Write failing arrangement tests**

Assert member rows expose pending/confirmed states; summary counts only pending AI decisions; an arrangement port edit converts pending states to manual overrides with reviewer evidence; unaffected confirmed/manual rows remain unchanged.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd v3_backend && pytest test_v2/section_9_web_api/test_arrangements.py -k port_resolution -v`

Expected: FAIL on missing projections/transition.

- [ ] **Step 3: Implement projections and shared override transition**

Do not duplicate JSON mutation in arrangements; call a service helper that applies manual override metadata without recursively generating one inquiry per member. Let the existing arrangement rematch execute once after all member updates.

- [ ] **Step 4: Run arrangement tests**

Run: `cd v3_backend && pytest test_v2/section_9_web_api/test_arrangements.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v3_backend/domains/orders/groups/arrangements.py v3_backend/domains/orders/groups/schemas.py v3_backend/test_v2/section_9_web_api/test_arrangements.py
git commit -m "feat: expose port review state in arrangements"
```

## Task 7: Frontend Contracts and Reusable Review UI

**Files:**
- Create: `v3-frontend/src/lib/port-resolution-view.ts`
- Create: `v3-frontend/src/lib/port-resolution-view.test.ts`
- Create: `v3-frontend/src/components/orders/PortResolutionReview.tsx`
- Modify: `v3-frontend/src/lib/orders-api.ts`
- Modify: `v3-frontend/src/lib/order-groups-api.ts`

**Interfaces:**
- Consumes: backend `PortResolutionState` and two endpoints.
- Produces: `portResolutionPresentation(state)` with label/tone/action flags; `confirmPortResolution(orderId, decisionId)`; `overridePortResolution(orderId, decisionId, portId)`; reusable review panel callbacks.

- [ ] **Step 1: Write failing view-helper tests**

Assert exact labels/tone for pending, confirmed, overridden/manual and unresolved; null legacy state renders no badge; pending exposes confirm/change actions; unresolved exposes change only.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd v3-frontend && pnpm test -- src/lib/port-resolution-view.test.ts`

Expected: FAIL on missing helper.

- [ ] **Step 3: Implement types, API calls, helper and compact component**

The component displays canonical port names supplied by application data, raw destination, model reason, and compact confirm/change controls. It never displays a model-returned name.

- [ ] **Step 4: Run frontend unit/type checks**

Run: `cd v3-frontend && pnpm test -- src/lib/port-resolution-view.test.ts && pnpm exec tsc --noEmit`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v3-frontend/src/lib/port-resolution-view.ts v3-frontend/src/lib/port-resolution-view.test.ts v3-frontend/src/components/orders/PortResolutionReview.tsx v3-frontend/src/lib/orders-api.ts v3-frontend/src/lib/order-groups-api.ts
git commit -m "feat: add ai port review frontend contract"
```

## Task 8: PO, Order List, Arrangement and Inquiry UX

**Files:**
- Modify: `v3-frontend/src/app/dashboard/orders/[id]/page.tsx`
- Modify: `v3-frontend/src/components/orders/OrderGroupsSection.tsx`
- Modify: `v3-frontend/src/app/dashboard/orders/arrangements/[id]/page.tsx`
- Modify: `v3-frontend/src/lib/arrangements-view.ts`
- Test: `v3-frontend/src/lib/arrangements-view.test.ts`
- Test: `v3-frontend/src/lib/order-workspace-view.test.ts`

**Interfaces:**
- Consumes: Task 7 helper/component and arrangement summary.
- Produces: visible AI badges, PO review actions, arrangement/inquiry warning banners, and port edits routed through override.

- [ ] **Step 1: Add failing view tests for summaries and copy**

Assert a pending member yields `1 个 PO 的目标港口由 AI 匹配，待人工确认`; confirmed/legacy rows do not; product actionable counts remain unchanged; inquiry controls remain enabled.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd v3-frontend && pnpm test -- src/lib/arrangements-view.test.ts src/lib/order-workspace-view.test.ts src/lib/port-resolution-view.test.ts`

Expected: FAIL on missing summary/presentation behavior.

- [ ] **Step 3: Integrate the UI**

Place the badge beside target port on PO/detail/member rows; render the compact review panel only for pending/unresolved states; route pending AI port edits to override; show one non-blocking banner above the inquiry area with no disabled controls.

- [ ] **Step 4: Run frontend tests, types and production build**

Run: `cd v3-frontend && pnpm test && pnpm exec tsc --noEmit && pnpm build`

Expected: all tests PASS, no type errors, Next.js build succeeds.

- [ ] **Step 5: Commit**

```bash
git add v3-frontend/src/app/dashboard/orders/[id]/page.tsx v3-frontend/src/components/orders/OrderGroupsSection.tsx v3-frontend/src/app/dashboard/orders/arrangements/[id]/page.tsx v3-frontend/src/lib/arrangements-view.ts v3-frontend/src/lib/arrangements-view.test.ts v3-frontend/src/lib/order-workspace-view.test.ts
git commit -m "feat: surface ai port review workflow"
```

## Task 9: Real-Model Evaluation and Historical Repair Tool

**Files:**
- Create: `v3_backend/test_v2/section_e2e/test_llm_port_resolution_eval.py`
- Create: `v3_backend/scripts/reprocess_unresolved_oracle_ports.py`
- Test: `v3_backend/test_v2/section_4_orders/test_port_resolution.py`

**Interfaces:**
- Consumes: resolver/service APIs.
- Produces: opt-in real Gemini evaluation; dry-run-first, explicit-PO repair command with guarded apply mode.

- [ ] **Step 1: Add failing script-contract and gated real-model tests**

Real eval cases: OSAKA=21, OKINAWA=25, TOKYO=28, SYDNEY=29, PORTLAND=unmatched, YOKOHAMA=unmatched, YOKOHAMA OSANBASHI=19. Script tests require `--po-number`, default to read-only/dry-run, reject bulk unbounded apply, and print before/after assertions.

- [ ] **Step 2: Run local non-network tests and verify script failure**

Run: `cd v3_backend && pytest test_v2/section_4_orders/test_port_resolution.py -k repair -v`

Expected: FAIL because the script entrypoint does not exist.

- [ ] **Step 3: Implement the guarded repair command and gated eval**

The command resolves one named Oracle order, prints source/destination/current state/proposed state, requires both `--apply` and `--expected-order-id` to write, and calls the same service rather than duplicating resolver logic.

- [ ] **Step 4: Run local tests and explicit real-model evaluation**

Run local: `cd v3_backend && pytest test_v2/section_4_orders/test_port_resolution.py -v`

Run opt-in: `cd v3_backend && RUN_REAL_GEMINI_TESTS=1 PYTEST_RUN_SLOW=1 pytest test_v2/section_e2e/test_llm_port_resolution_eval.py -v`

Expected: local PASS; real eval passes by `status/port_id`, not reason text.

- [ ] **Step 5: Commit**

```bash
git add v3_backend/scripts/reprocess_unresolved_oracle_ports.py v3_backend/test_v2/section_e2e/test_llm_port_resolution_eval.py v3_backend/test_v2/section_4_orders/test_port_resolution.py
git commit -m "test: add llm port evaluation and repair guard"
```

## Task 10: Full Verification, Review and Release Preparation

**Files:**
- Create: `docs/llm-port-resolution-2026-09-27/PROGRESS.md`
- Modify outside this repository after deployment status is known: `/Users/yichuanzhang/Desktop/curise_system_2/PROGRESS.md`
- Create outside this repository only after production evidence exists: `/Users/yichuanzhang/Desktop/curise_system_2/DEPLOYMENT_VERIFIED_2026-09-27_LLM_PORT_RESOLUTION.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: a reviewed release candidate, migration evidence, deployment evidence, and PO168798CCI production verification.

- [ ] **Step 1: Run backend focused and full suites**

Run focused:

```bash
cd v3_backend
pytest test_v2/section_4_orders/test_port_resolution.py test_v2/section_4_orders/test_oracle_import.py test_v2/section_4_orders/test_order_issue_overview.py test_v2/section_9_web_api/test_orders_api.py test_v2/section_9_web_api/test_arrangements.py -v
ruff check domains/orders/port_resolution domains/orders/anomaly.py domains/orders/matching/automation.py apps/jobs/oracle_po.py apps/http/orders.py
```

Run full: `cd v3_backend && pytest`

Expected: all non-explicit external tests PASS; only documented external skips remain.

- [ ] **Step 2: Verify migration upgrade/downgrade/upgrade on disposable PostgreSQL**

Run:

```bash
docker run --name cruise-port-resolution-pg -e POSTGRES_PASSWORD=cruise_test -e POSTGRES_DB=cruise_port_resolution -p 55432:5432 -d postgres:16
until docker exec cruise-port-resolution-pg pg_isready -U postgres -d cruise_port_resolution; do sleep 1; done
cd v3_backend
DATABASE_URL=postgresql+psycopg2://postgres:cruise_test@127.0.0.1:55432/cruise_port_resolution alembic upgrade 0031_unit_conversion_rules
DATABASE_URL=postgresql+psycopg2://postgres:cruise_test@127.0.0.1:55432/cruise_port_resolution alembic upgrade 0032_llm_port_resolution
DATABASE_URL=postgresql+psycopg2://postgres:cruise_test@127.0.0.1:55432/cruise_port_resolution alembic downgrade 0031_unit_conversion_rules
DATABASE_URL=postgresql+psycopg2://postgres:cruise_test@127.0.0.1:55432/cruise_port_resolution alembic upgrade 0032_llm_port_resolution
DATABASE_URL=postgresql+psycopg2://postgres:cruise_test@127.0.0.1:55432/cruise_port_resolution alembic current
docker rm -f cruise-port-resolution-pg
```

Before the first `0032` upgrade, insert one disposable user/order fixture and record its non-resolution fields; assert the same values after each transition. Inspect all five new columns, both check constraints, the reviewer foreign key, and final head `0032_llm_port_resolution`.

Expected: data outside new nullable columns is unchanged; downgrade removes only the new columns/constraints.

- [ ] **Step 3: Run frontend full verification**

Run: `cd v3-frontend && pnpm test && pnpm exec tsc --noEmit && pnpm build`

Expected: PASS.

- [ ] **Step 4: Run architecture/package checks and inspect the complete diff**

Run:

```bash
cd v3_backend
python scripts/check_arch.py
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir /tmp/cruise-port-resolution-wheel
cd ..
git diff --check
git status --short
rg -n "port_id\s*=|update\([^)]*port_id|['\"]port_id['\"]" v3_backend/domains v3_backend/apps
```

Review every reported write path so none can leave a stale pending decision.

Expected: zero architecture violations, clean diff formatting, only scoped files changed.

- [ ] **Step 5: Request whole-branch code review and fix only evidence-backed findings**

Use `superpowers:requesting-code-review`, then rerun the affected focused tests and the full verification commands after accepted fixes.

- [ ] **Step 6: Commit progress and release-readiness documentation**

Create `docs/llm-port-resolution-2026-09-27/PROGRESS.md` and record implemented behavior, exact test counts, known limitations, migration pending status, and that production is not yet changed. Do not update the workspace-level production progress record before deployment evidence exists.

- [ ] **Step 7: Execute controlled production release**

Follow the design's sequence: production backup; 0032 migration; candidate backend with LLM flag on; health/auth/CORS/read-only schema and shadow-model checks; traffic switch; Oracle Job same digest; frontend deployment; scheduler observation. Do not run historical repair until candidate and scheduled paths are healthy.

- [ ] **Step 8: Repair and verify PO168798CCI first**

Dry-run the guarded command, assert proposed `port_id=21`, then apply with exact order ID. Verify product `99PRD010318` matches, correct arrangement/inquiry exists, `pending_review` is visible, and no false `PRODUCT_NOT_MATCHED` remains.

- [ ] **Step 9: Repair remaining OSAKA/OKINAWA orders one by one**

For each PO: dry-run, assert proposed port, apply, and record final matching/group/inquiry outcome. Stop on the first unexpected result rather than bulk continuing.

- [ ] **Step 10: Write final production verification and update progress**

Record immutable image digest, Cloud Run revision/traffic, Oracle Job generation, Vercel deployment, DB head, backup ID, execution IDs, test counts, PO repair evidence, scheduler result and rollback targets. Append the verified production state to `/Users/yichuanzhang/Desktop/curise_system_2/PROGRESS.md`; do not rewrite older dated records.
