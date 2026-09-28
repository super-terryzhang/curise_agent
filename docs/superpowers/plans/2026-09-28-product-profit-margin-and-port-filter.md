# Product Profit Margin and Port Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only product gross-margin percentage to every product view and add a server-backed port filter to product management, then release the verified change without a database migration.

**Architecture:** The backend derives `profit_margin` from the same `price` and `contract_price` values already serialized for the page; no value is persisted. A focused frontend profit-margin module owns preview calculation, formatting, and color semantics, while `ProductsTab` wires the existing `port_id` API contract into one shared filter state used by list, gallery, pagination, and export.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, Pydantic, pytest, TypeScript, React 19, Next.js 16, Vitest, pnpm.

**Spec:** `docs/superpowers/specs/2026-09-28-product-profit-margin-design.md`

## Global Constraints

- Formula: `profit_margin = (contract_price - price) / contract_price * 100`.
- Use product master-record `price` and `contract_price`; do not resolve price periods or exchange rates.
- Return/display no margin when either price is missing or `contract_price <= 0`; preserve zero, 100%, and negative margins.
- Add no database column, migration, history event, upload/export column, profit filter, or profit sort.
- Port filtering must use the existing `port_id` backend contract and existing `listPorts()` data; never infer or hard-code a port.
- The selected port combines with search/category/supplier/country/status, survives list/gallery switches, resets pagination, and applies to filtered Excel export.
- Do not change order finance, matching, inquiry, price-period, image, permission, Scheduler, or production data behavior.
- Follow `EXPERIENCE.md`: targeted tests during tasks, one local full regression for the stable candidate, one GitHub `main` CI, and no duplicate full suite for documentation-only changes.

## Review Focus

- Missing prices, empty form strings, non-finite values, and selling price zero must yield “未配置”, never `NaN%` or `Infinity%`; Task 2 tests all forms.
- Purchase price greater than selling price must remain a negative percentage with the loss color; Tasks 1 and 2 pin `100 / 80 -> -25.00%`.
- Create/edit preview and saved API output must agree after two-decimal formatting; Task 2 checks string inputs and backend-provided values with the same examples.
- Invalid or tampered port values must never produce a malformed query; Task 3 tests `all`, invalid, non-positive, and positive ID strings.
- A port change on a later page must reset to page zero, remain active after view changes, and flow into export; Task 3 tests the query contract and Task 4 verifies the interaction in the built page.

---

### Task 1: Backend Read-Only Profit Margin Contract

**Files:**
- Modify: `v3_backend/domains/masterdata/_products_service.py:299-355`
- Modify: `v3_backend/domains/masterdata/schemas.py:225-265`
- Modify: `v3_backend/test_v2/section_6_masterdata/test_crud_service.py:98-178`
- Modify: `v3_backend/test_v2/section_9_web_api/test_masterdata_api.py:83-128`

**Interfaces:**
- Consumes: `Product.price: Decimal | None`, `Product.contract_price: Decimal | None`.
- Produces: `_calculate_profit_margin(price: Decimal | None, contract_price: Decimal | None) -> float | None`.
- Produces for Task 2: `ProductResponse.profit_margin: float | None` and product JSON field `profit_margin`.

- [ ] **Step 1: Write failing service tests for the calculation matrix**

Add parametrized `test_product_profit_margin_is_derived_from_display_prices` cases for `(80,100)->20`, `(100,100)->0`, `(100,80)->-25`, `(0,100)->100`, missing purchase, missing selling, and selling zero. Assert create/list serialization contains the expected number or `None`.

- [ ] **Step 2: Run the focused service test and verify it fails because the field is absent**

Run:

```bash
cd v3_backend
/Users/yichuanzhang/Desktop/curise_system_2/curise_agent/v3_backend/.venv/bin/pytest test_v2/section_6_masterdata/test_crud_service.py -k profit_margin -q
```

Expected: FAIL on the missing response key.

- [ ] **Step 3: Implement the backend derived field**

Add the helper beside product serialization, calculate with `Decimal`, round the percentage to two decimal places, and return `None` for missing values or a non-positive selling price. Add `profit_margin` to `serialize(...)` and `ProductResponse`; do not add it to create/update schemas or ORM models.

- [ ] **Step 4: Add an HTTP contract test proving PATCH recomputes the field**

Add `test_patch_product_recomputes_profit_margin`: patch `price` and `contract_price`, assert `20.0`, list the product, and assert the same value. This pins the field as derived rather than stored.

- [ ] **Step 5: Run targeted backend checks**

```bash
cd v3_backend
/Users/yichuanzhang/Desktop/curise_system_2/curise_agent/v3_backend/.venv/bin/pytest test_v2/section_6_masterdata/test_crud_service.py test_v2/section_9_web_api/test_masterdata_api.py -k 'profit_margin or list_products_validates_and_applies_sort' -q
/Users/yichuanzhang/Desktop/curise_system_2/curise_agent/v3_backend/.venv/bin/python scripts/check_arch.py
```

Expected: selected tests pass and architecture reports zero violations.

- [ ] **Step 6: Commit**

```bash
git add v3_backend/domains/masterdata/_products_service.py v3_backend/domains/masterdata/schemas.py v3_backend/test_v2/section_6_masterdata/test_crud_service.py v3_backend/test_v2/section_9_web_api/test_masterdata_api.py
git commit -m "feat: expose product profit margin"
```

### Task 2: Shared Profit Margin Presentation

**Files:**
- Create: `v3-frontend/src/components/data/product-profit-margin.tsx`
- Create: `v3-frontend/src/components/data/product-profit-margin.test.tsx`
- Modify: `v3-frontend/src/lib/data-api.ts:7-60`
- Modify: `v3-frontend/src/components/data/product-gallery-grid.tsx:205-269`
- Modify: `v3-frontend/src/components/data/product-gallery-grid.test.tsx`
- Modify: `v3-frontend/src/app/dashboard/data/ProductsTab.tsx:607-675,1043-1080`

**Interfaces:**
- Consumes from Task 1: `ProductItem.profit_margin: number | null`.
- Produces: `calculateProductProfitMargin(purchase: number | string | null | undefined, selling: number | string | null | undefined): number | null`.
- Produces: `ProductProfitMargin({ value, emptyLabel? })`, a read-only percentage renderer.

- [ ] **Step 1: Write failing frontend unit/render tests**

Test the calculator with normal, zero, negative, 100%, empty, missing, non-numeric, non-finite, and selling-zero inputs. Render the component and assert `20.00%`, `0.00%`, `-25.00%`, “未配置”, and positive/neutral/negative/muted class names.

- [ ] **Step 2: Run the new test and verify it fails because the module is absent**

```bash
cd v3-frontend
pnpm vitest run src/components/data/product-profit-margin.test.tsx
```

- [ ] **Step 3: Implement the shared calculator and renderer**

Match backend edge semantics, round preview values to two decimals, render exactly two fractional digits, and apply semantic text colors without a writable input.

- [ ] **Step 4: Wire API type, list, gallery, and form preview**

Add nullable `profit_margin` to `ProductItem`. Add the list column immediately after “卖价”, add the renderer near gallery selling price, and add a read-only “利润率” block in the price row using live form strings. Saved list/gallery values use the backend field.

- [ ] **Step 5: Extend gallery tests and run focused checks**

```bash
cd v3-frontend
pnpm vitest run src/components/data/product-profit-margin.test.tsx src/components/data/product-gallery-grid.test.tsx
pnpm exec tsc --noEmit
```

Expected: tests and TypeScript pass.

- [ ] **Step 6: Commit**

```bash
git add v3-frontend/src/components/data/product-profit-margin.tsx v3-frontend/src/components/data/product-profit-margin.test.tsx v3-frontend/src/components/data/product-gallery-grid.tsx v3-frontend/src/components/data/product-gallery-grid.test.tsx v3-frontend/src/app/dashboard/data/ProductsTab.tsx v3-frontend/src/lib/data-api.ts
git commit -m "feat: show product profit margin"
```

### Task 3: Product Port Filter

**Files:**
- Create: `v3-frontend/src/lib/product-filter-query.ts`
- Create: `v3-frontend/src/lib/product-filter-query.test.ts`
- Modify: `v3-frontend/src/app/dashboard/data/ProductsTab.tsx:177-244,326-335,752-818`
- Modify: `v3-frontend/src/lib/product-list-api.test.ts`

**Interfaces:**
- Consumes: existing `listProducts({ port_id })`, `listPorts()`, and `getFilterParams(...)`.
- Produces: `normalizeProductPortFilter(value: string): number | undefined`; `"all"`, non-integers, and non-positive values omit the query, while a positive ID string returns its integer.
- Produces: `filterPort: string`, using `"all"` or a port ID string.

- [ ] **Step 1: Write failing normalization and API-query tests**

Test `"all" -> undefined`, `"19" -> 19`, `"20" -> 20`, invalid/non-positive values -> `undefined`, and `listProducts({port_id: 19})` producing `port_id=19` alongside pagination/sort. Independent `19` and `20` results pin ID-based selection even if two ports later share a display name.

- [ ] **Step 2: Run focused tests and verify the helper test fails**

```bash
cd v3-frontend
pnpm vitest run src/lib/product-filter-query.test.ts src/lib/product-list-api.test.ts
```

- [ ] **Step 3: Implement ID-backed port filtering**

Add `filterPort`, a “全部港口” `Select` between country and status, ID-string options from `ports`, normalization into `params.port_id`, and `filterPort` to callback/effect dependencies. The existing effect resets pagination; list/gallery switches retain the filter. Add no backend code.

- [ ] **Step 4: Prove the shared filter path covers list, gallery, and export**

Confirm both `fetchProducts` and `exportProductPrices` call the same `getFilterParams`. Add API assertions for combined `port_id`, `country_id`, `is_effective`, sort, limit, and offset parameters.

- [ ] **Step 5: Run all product-management targeted tests**

```bash
cd v3-frontend
pnpm vitest run src/lib/product-filter-query.test.ts src/lib/product-list-api.test.ts src/components/data/product-profit-margin.test.tsx src/components/data/product-gallery-grid.test.tsx src/components/data/product-gallery-query.test.ts
pnpm exec tsc --noEmit
```

- [ ] **Step 6: Commit**

```bash
git add v3-frontend/src/lib/product-filter-query.ts v3-frontend/src/lib/product-filter-query.test.ts v3-frontend/src/lib/product-list-api.test.ts v3-frontend/src/app/dashboard/data/ProductsTab.tsx
git commit -m "feat: filter products by port"
```

### Task 4: Stable Candidate, Review, PR, and Release

**Files:**
- Modify only if review finds a scoped defect: files listed in Tasks 1-3.
- Create after successful deployment: workspace-root dated deployment verification and scoped progress updates following current project convention.

**Interfaces:**
- Consumes: Tasks 1-3 complete on `feature/product-profit-margin-20260928`.
- Produces: a reviewed PR, green `main`, immutable backend/frontend production artifacts, and a dated production record.

- [ ] **Step 1: Run one local stable-candidate verification**

Run the frontend full test suite, TypeScript, and production build once. Run backend architecture, changed-file Ruff, full pytest once, and wheel build/install/resource import once. Do not repeat full suites unless a review fix changes shared behavior.

- [ ] **Step 2: Review the whole branch**

Use `superpowers:requesting-code-review`. Review the diff from `origin/main`, verify no migration or unrelated file changed, and address only evidence-backed findings with targeted tests.

- [ ] **Step 3: Push and open the feature PR**

Record formula, empty/zero behavior, three display locations, ID-backed port filtering, no migration, and exact test results in the PR.

- [ ] **Step 4: Merge after PR checks, then wait for the single `main` CI**

Record merge SHA and run ID. Do not trigger another full application run for documentation-only follow-up.

- [ ] **Step 5: Re-read current production state**

Because `main` contains releases after the earlier 2026-09-27 baseline, query current Cloud Run revision/image, Vercel deployment, Oracle Job generation, database head, and schedulers. Preserve every newer change and use the merged main SHA as artifact source.

- [ ] **Step 6: Deploy without a database migration**

Build one immutable backend image from the exact merged main archive. Verify a 0%-traffic candidate for health, auth, CORS, `profit_margin`, and port filtering before 100% cutover; synchronize the Oracle Job image according to current convention without manually executing a scan. Build and verify a Vercel production candidate before promotion.

- [ ] **Step 7: Perform read-only production acceptance**

Verify known products show the same two-decimal margin in list/gallery/edit, missing/negative behavior when available, and one real port filter in both views. Confirm totals change only through filtering and perform no product write.

- [ ] **Step 8: Record the release**

Update root `AGENTS.md`, root `PROGRESS.md`, a dated `DEPLOYMENT_VERIFIED_*.md`, and scoped repository progress. Use a documentation-only PR and do not repeat full application regression.
