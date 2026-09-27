# Order Management Dual View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the arrangement-only order list with switchable, filterable and paginated PO and voyage views, including truthful search over raw PO product names.

**Architecture:** Keep the existing permission-scoped arrangement endpoint and grouping model. Extend each lightweight PO summary with deduplicated raw product names, then build deterministic pure frontend view-model functions that feed focused PO/voyage table components and preserve all existing mutation APIs.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, pytest, Next.js 16, React 19, TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-27-order-management-dual-view-design.md`

## Global Constraints

- Work only in `feature/order-management-dual-view-20260927` and its isolated worktree.
- Do not modify database schema, matching, grouping, inquiry, unit conversion or production resources.
- Product-name search uses only `Order.products[].product_name`; trim only outer whitespace and never infer, synthesize or rewrite internal text.
- Preserve the current permission scope and return-all-members behavior of `/api/order-groups/arrangements`.
- Preserve existing PO/group mutations and refresh after successful writes.
- Follow TDD for every production behavior and keep the branch reviewable through focused commits.

## Review Focus

- A PO with blank, duplicate or non-string product names returns a stable unique string list and does not crash the summary endpoint (Task 1 test).
- Product-name search is case-insensitive but does not accidentally match another PO's products (Task 2 test).
- Combined search, ship, port, date and status filters reset pagination and return only rows satisfying every active filter (Task 2 tests).
- A group containing missing-info, failed, processing and actionable POs receives the highest-priority status and the correct current actionable sum (Task 2 test).
- Pagination clamps after filtering so deleting or reclassifying the last row cannot leave the user on an empty out-of-range page (Task 2 test).

---

## File Structure

### Backend modifications

- `v3_backend/domains/orders/groups/arrangements.py` — expose canonical raw PO product names in existing summaries.
- `v3_backend/test_v2/section_9_web_api/test_arrangements.py` — lock the summary contract, permissions and malformed-name behavior.

### Frontend additions

- `v3-frontend/src/lib/order-management-view.ts` — pure normalization, status, filtering, sorting and pagination.
- `v3-frontend/src/lib/order-management-view.test.ts` — deterministic view-model tests.
- `v3-frontend/src/components/orders/OrderManagementTables.tsx` — PO and voyage tables with existing actions expressed as callbacks.
- `v3-frontend/src/components/orders/OrderManagementTables.test.tsx` — server-rendered accessibility/content tests for both tables.

### Frontend modifications

- `v3-frontend/src/lib/order-groups-api.ts` — type the new `product_names` summary field.
- `v3-frontend/src/app/dashboard/orders/page.tsx` — own dual-view state, filters, pagination and existing write dialogs.

## Task 1: Truthful Product Search Contract

**Files:**
- Modify: `v3_backend/test_v2/section_9_web_api/test_arrangements.py`
- Modify: `v3_backend/domains/orders/groups/arrangements.py:list_arrangements`
- Modify: `v3-frontend/src/lib/order-groups-api.ts:ArrangementOrder`

**Interfaces:**
- Produces backend field: `product_names: list[str]` on every arrangement/unclassified PO summary.
- Produces frontend field: `ArrangementOrder.product_names: string[]`.
- Consumes only stored `Order.products` rows with a non-empty string `product_name`.

- [x] **Step 1: Add a failing API contract test**

Add `test_arrangement_summary_exposes_unique_raw_product_names` with two visible orders. Assert ordered deduplication, whitespace normalization, empty/malformed values ignored, an empty list for no products, and no foreign-user product names in the response.

- [x] **Step 2: Run the focused backend test and verify RED**

Run: `cd v3_backend && pytest test_v2/section_9_web_api/test_arrangements.py::test_arrangement_summary_exposes_unique_raw_product_names -v`

Expected: FAIL because `product_names` is absent.

- [x] **Step 3: Implement the minimal summary field**

Add `Order.products` to the summary query and a private `_raw_product_names(products: object) -> list[str]` helper. Accept only list entries that are dictionaries with non-empty string `product_name`; trim, deduplicate case-insensitively while preserving the first stored spelling and order. Attach the result to every member summary.

- [x] **Step 4: Type the frontend contract and verify GREEN**

Add required `product_names: string[]` to `ArrangementOrder` and update existing test fixtures. Run:

`cd v3_backend && pytest test_v2/section_9_web_api/test_arrangements.py -v`

Expected: all arrangement API tests PASS.

- [ ] **Step 5: Commit**

```bash
git add v3_backend/domains/orders/groups/arrangements.py v3_backend/test_v2/section_9_web_api/test_arrangements.py v3-frontend/src/lib/order-groups-api.ts v3-frontend/src/lib/arrangements-view.test.ts
git commit -m "feat: expose PO product names in arrangement summaries"
```

## Task 2: Dual-View Read Model

**Files:**
- Create: `v3-frontend/src/lib/order-management-view.ts`
- Create: `v3-frontend/src/lib/order-management-view.test.ts`

**Interfaces:**
- Consumes: `ArrangementsResult`, `ArrangementOrder`, `SupplyArrangement`.
- Produces: `ManagementView`, `ManagementStatus`, `ManagementFilters`, `PoManagementRow`, `VoyageManagementRow`, `StatusPresentation`.
- Produces functions: `buildPoRows`, `buildVoyageRows`, `filterPoRows`, `filterVoyageRows`, `managementFilterOptions`, `paginateRows`, `weekdayLabel`.

- [x] **Step 1: Write failing normalization and status tests**

Cover: flattening classified and unclassified POs exactly once; missing-info priority; failure before processing; processing before actionable; current actionable labels/counts; voyage product/PO aggregation; unclassified special row; mixed-member highest-priority status.

- [x] **Step 2: Run the new test file and verify RED**

Run: `cd v3-frontend && pnpm test -- src/lib/order-management-view.test.ts`

Expected: FAIL because the module does not exist.

- [x] **Step 3: Implement minimal normalization and status functions**

Use explicit priority values and immutable derived rows. A PO is missing information when ship, loading day or selected port is absent. Never derive product issues from absent match results.

- [x] **Step 4: Add failing filter, sort and pagination tests**

Cover PO raw product-name search isolation and case folding; voyage ship/port search; combined exact filters and inclusive date range; internal `unclassifiedOnly` scope; PO missing-first/date-desc sort; voyage date-asc/unclassified-last sort; unique options; empty results; page clamping and slice boundaries.

- [x] **Step 5: Implement filters, sorting, options and `paginateRows<T>`**

`paginateRows` returns `{ items, page, pageCount, total }`, with page clamped to `1..max(1, pageCount)`. All active filters are ANDed. Empty date rows fail an active date constraint.

- [x] **Step 6: Run view-model tests and verify GREEN**

Run: `cd v3-frontend && pnpm test -- src/lib/order-management-view.test.ts`

Expected: all new tests PASS.

- [ ] **Step 7: Commit**

```bash
git add v3-frontend/src/lib/order-management-view.ts v3-frontend/src/lib/order-management-view.test.ts
git commit -m "feat: add order management dual-view model"
```

## Task 3: Direct PO and Voyage Tables

**Files:**
- Create: `v3-frontend/src/components/orders/OrderManagementTables.tsx`
- Create: `v3-frontend/src/components/orders/OrderManagementTables.test.tsx`
- Modify: `v3-frontend/src/app/dashboard/orders/page.tsx`

**Interfaces:**
- Consumes Task 2 rows and status presentations.
- `PoManagementTable` receives paged rows plus `busy`, `onAssign`, `onRemove`, `onReclassify`, `onDelete` callbacks.
- `VoyageManagementTable` receives paged rows and `onShowUnclassified`; normal rows link to existing arrangement detail routes.
- The page owns `poFilters`, `voyageFilters`, `poPage`, `voyagePage`, page sizes and selected view.

- [x] **Step 1: Add failing server-rendered table tests**

Use `react-dom/server` to assert the PO table exposes all required column headings, paged rows, status text and PO detail link; assert the voyage table exposes PO/product totals, weekday text, arrangement link and the special unclassified action.

- [x] **Step 2: Run component tests and verify RED**

Run: `cd v3-frontend && pnpm test -- src/components/orders/OrderManagementTables.test.tsx`

Expected: FAIL because the table module does not exist.

- [x] **Step 3: Implement both focused table components**

Use semantic tables, visible text status chips, horizontal overflow on narrow screens and existing authenticated mutation callbacks. Do not duplicate filtering or status logic in JSX.

- [x] **Step 4: Replace the expandable page with dual-view controls**

Add the `按 PO / 按轮次` switcher, view-specific description, search/date/select controls, reset, refresh, counts, page-size selectors and previous/next controls. Each view owns independent filters and pagination. Keep the existing assignment/create-group/delete dialog and refresh behavior. The unclassified voyage action switches to PO view with an explicit internal `unclassifiedOnly` filter, shows that scope in the result summary, and lets reset clear it.

- [x] **Step 5: Run focused tests, type checking and verify GREEN**

Run:

- `cd v3-frontend && pnpm test -- src/lib/order-management-view.test.ts src/components/orders/OrderManagementTables.test.tsx src/lib/arrangements-view.test.ts`
- `cd v3-frontend && pnpm exec tsc --noEmit`

Expected: tests PASS and TypeScript exits 0.

- [ ] **Step 6: Commit**

```bash
git add v3-frontend/src/app/dashboard/orders/page.tsx v3-frontend/src/components/orders/OrderManagementTables.tsx v3-frontend/src/components/orders/OrderManagementTables.test.tsx
git commit -m "feat: add PO and voyage order management views"
```

## Task 4: Complete Verification and Delivery

**Files:**
- Modify: `docs/superpowers/plans/2026-09-27-order-management-dual-view.md` only to mark completed steps if needed.
- Inspect: complete branch diff against `origin/main`.

**Interfaces:**
- Consumes all earlier tasks.
- Produces a review-ready branch and Pull Request; does not merge or deploy.

- [ ] **Step 1: Run focused backend quality checks**

Run:

- `cd v3_backend && ruff check domains/orders/groups/arrangements.py test_v2/section_9_web_api/test_arrangements.py`
- `cd v3_backend && python scripts/check_arch.py`

Expected: both exit 0.

- [ ] **Step 2: Run complete backend tests**

Run: `cd v3_backend && pytest -q`

Expected: no failures; report passes, skips and warnings exactly.

- [ ] **Step 3: Run complete frontend verification**

Run:

- `cd v3-frontend && pnpm test`
- `cd v3-frontend && pnpm exec tsc --noEmit`
- `cd v3-frontend && pnpm build`

Expected: all tests PASS, TypeScript exits 0 and production build succeeds.

- [ ] **Step 4: Review complete diff and requirements**

Inspect `git diff --check`, `git diff --stat origin/main...HEAD`, the full diff, the spec checklist and all review-focus cases. Confirm no database, matching, grouping, inquiry or deployment files changed.

- [ ] **Step 5: Request whole-branch code review**

Review `origin/main...HEAD` against the spec and plan. Because this task does not authorize sub-agent delegation, perform and record a full self-review instead of claiming independent review. Fix Critical/Important findings with a RED→GREEN test and repeat the affected full suite; record any deferred Minor findings.

- [ ] **Step 6: Final commit, push and Pull Request**

Commit final documentation if changed, push `feature/order-management-dual-view-20260927`, and open a Pull Request to `main` describing the two views, data-contract addition and verification evidence. Do not merge or deploy.

- [ ] **Step 7: Verify GitHub automatic tests**

Wait for the PR `backend` and `frontend` checks to finish. Delivery is complete only when both checks succeed; otherwise diagnose and fix on the feature branch.
