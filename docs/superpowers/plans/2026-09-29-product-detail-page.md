# Product Detail Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one routable product detail page that unifies existing product fields, independent price periods/history, and per-product images without adding database fields.

**Architecture:** Add one read-only product-detail API on top of the existing serializer, then compose focused frontend panels under `/dashboard/data/products/[id]`. Reuse existing write APIs and extract the current inline form/dialog bodies instead of duplicating business rules.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, Next.js 16 App Router, React 19, TypeScript, Tailwind, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-09-29-product-detail-page-design.md`

## Global Constraints

- No database migration and no new product, price, audit, or image fields.
- Purchase and selling periods remain independent; overlap is forbidden only within the same price type.
- Read access stays available to employee; writes stay limited to admin and superadmin.
- The image tab exposes only upload, view, delete, image count, and current-primary display.
- Preserve legacy product deep links by redirecting them to the new route.
- Run focused tests per task, one full local regression near the end, and one CI regression after push.

## Review Focus

- Direct refresh of an existing product detail URL loads the product without relying on list pagination.
- Missing or deleted product IDs show a stable 404 state instead of an endless loader.
- Read-only users never receive edit, price-write, upload, or delete controls.
- Legacy `product/action` links map edit to `basic&edit=1`, prices to `prices`, and no action to `basic`.
- A successful edit/upload/price mutation refreshes only the affected detail data without returning to the list.

---

### Task 1: Stable product-detail API contract

**Files:**
- Modify: `v3_backend/domains/masterdata/_products_service.py`
- Modify: `v3_backend/domains/masterdata/service.py`
- Modify: `v3_backend/apps/http/masterdata.py`
- Modify: `v3_backend/test_v2/section_6_masterdata/test_crud_service.py`
- Modify: `v3_backend/test_v2/section_9_web_api/test_masterdata_api.py`
- Modify: `v3-frontend/src/lib/data-api.ts`
- Modify: `v3-frontend/src/lib/product-list-api.test.ts`

**Interfaces:**
- Produces: backend `get_product(db: Session, product_id: int) -> dict[str, Any]` and frontend `getProduct(productId: number): Promise<ProductItem>`.
- Consumes: existing `repo.get_product`, `serialize`, `ProductResponse`, and authenticated `api<T>` helper.

- [ ] Write failing service/API tests proving full serialization, authenticated employee read access, and 404 for an unknown ID.
- [ ] Run the new backend tests and confirm they fail because the service/route do not exist.
- [ ] Implement `get_product` and `GET /products/{product_id}` before the nested price/image routes.
- [ ] Write and run a failing frontend API test for `GET /api/data/products/42`, then implement `getProduct`.
- [ ] Run backend masterdata API/service tests and the frontend API test; expect all pass.
- [ ] Commit as `feat: expose product detail endpoint`.

### Task 2: Shared product form and truthful basic-information panel

**Files:**
- Create: `v3-frontend/src/components/data/product-form-dialog.tsx`
- Create: `v3-frontend/src/components/data/product-form.test.ts`
- Create: `v3-frontend/src/components/data/product-basic-info.tsx`
- Create: `v3-frontend/src/components/data/product-basic-info.test.tsx`
- Modify: `v3-frontend/src/app/dashboard/data/ProductsTab.tsx`

**Interfaces:**
- Produces: `ProductFormDialog` for create/edit and `ProductBasicInfo({ product })` for read-only display.
- Consumes: existing product/reference DTOs, `createProduct`, `updateProduct`, margin calculation, and current validation copy.

- [ ] Write failing tests for form mapping/payload validation and for the exact existing fields rendered by `ProductBasicInfo`.
- [ ] Extract the current form state, date validation, payload mapping and dialog JSX without changing create/edit behavior.
- [ ] Replace the inline `ProductsTab` form with `ProductFormDialog`; remove duplicate state and the duplicate loading spinner.
- [ ] Implement the basic-information panel without prices or invented metadata.
- [ ] Run focused form/basic-info tests and existing profit-margin tests; expect all pass.
- [ ] Commit as `refactor: share product form and basic information`.

### Task 3: Embedded price management and audit panels

**Files:**
- Modify: `v3-frontend/src/components/data/product-price-periods.tsx`
- Modify: `v3-frontend/src/components/data/product-price-history.tsx`
- Create: `v3-frontend/src/components/data/product-price-panels.test.tsx`

**Interfaces:**
- Produces: `ProductPricePeriodsPanel` and `ProductPriceHistoryPanel`, each independently loadable and refreshable.
- Consumes: existing period CRUD, audit pagination/filter/restore APIs, and `canEdit` permission flag.

- [ ] Write failing render tests for separate purchase/selling groups, read-only suppression, empty/error states, and existing audit controls.
- [ ] Extract dialog bodies into embedded panels while preserving validation, pagination, restore and mutation callbacks.
- [ ] Present purchase and selling periods as separate sections and keep a single shared editor whose type is locked during edit.
- [ ] Run price panel tests plus existing price-history tests; expect all pass.
- [ ] Commit as `refactor: embed product price management panels`.

### Task 4: Embedded product-image panel

**Files:**
- Modify: `v3-frontend/src/components/data/product-images-gallery.tsx`
- Create: `v3-frontend/src/components/data/product-images-gallery.test.tsx`

**Interfaces:**
- Produces: `ProductImagesPanel({ productId, productName, readOnly, onImagesChanged })`.
- Consumes: existing list/upload/delete APIs and the existing lightbox.

- [ ] Write failing tests for image count, primary badge, accepted file types, read-only controls, empty state, and hover action labels.
- [ ] Extract the gallery body from its dialog and render it as a compact embedded panel.
- [ ] Preserve sequential uploads, per-file failure reporting, lightbox navigation, confirmation before delete, and parent refresh callback.
- [ ] Remove stale UI comments that describe deferred capabilities as if they were available.
- [ ] Run the new gallery test and existing image API/order tests; expect all pass.
- [ ] Commit as `refactor: embed product image management`.

### Task 5: Product detail route and deep-link integration

**Files:**
- Create: `v3-frontend/src/app/dashboard/data/products/[id]/page.tsx`
- Create: `v3-frontend/src/components/data/product-detail-page.tsx`
- Create: `v3-frontend/src/components/data/product-detail-page.test.tsx`
- Create: `v3-frontend/src/lib/product-detail-route.ts`
- Create: `v3-frontend/src/lib/product-detail-route.test.ts`
- Modify: `v3-frontend/src/app/dashboard/data/page.tsx`
- Modify: `v3-frontend/src/app/dashboard/data/ProductsTab.tsx`
- Modify: `v3-frontend/src/components/data/product-gallery-grid.tsx`
- Modify: `v3-frontend/src/components/data/product-gallery-grid.test.tsx`
- Modify: `v3-frontend/src/lib/order-issue-view.ts`
- Modify: `v3-frontend/src/lib/order-issue-view.test.ts`
- Modify: `v3-frontend/src/app/dashboard/data/UnitConversionRulesTab.tsx`

**Interfaces:**
- Produces: `/dashboard/data/products/{id}?tab=basic|prices|images[&edit=1]` and pure route-normalization helpers.
- Consumes: Tasks 1–4 interfaces and current role/auth helpers.

- [ ] Write failing route-helper and static-render tests for valid/invalid tabs, 404/error UI, permissions, and old-link mapping.
- [ ] Build the shared header, three query-backed tabs, loading/error states and edit dialog on the detail page.
- [ ] Route product list/gallery image, product name, price-history, manage-price and edit actions to the relevant detail state.
- [ ] Update PO issue and unit-conversion links, and add a compatibility redirect for old `product/action` URLs.
- [ ] Remove `historyProduct`, `periodProduct`, `galleryProduct`, `initialProductId`, `initialAction` and the obsolete dialog mounts from `ProductsTab`.
- [ ] Run all affected frontend tests and TypeScript; expect all pass.
- [ ] Commit as `feat: unify product detail workflow`.

### Task 6: Consolidation, full verification, review and progress record

**Files:**
- Modify: `/Users/yichuanzhang/Desktop/curise_system_2/PROGRESS.md`
- Modify if needed: touched source/tests only; no unrelated refactor.

**Interfaces:**
- Consumes: all prior task outputs.
- Produces: review-ready branch plus explicit “not deployed” progress record.

- [ ] Run focused backend masterdata/image/price tests and focused frontend product tests.
- [ ] Run Ruff on touched backend files and `pnpm exec tsc --noEmit` plus `pnpm build`.
- [ ] Run the backend full suite once and frontend full suite once; record exact pass/skip/failure counts.
- [ ] Review the whole branch against the approved mockups and spec; fix Critical/Important findings with RED→GREEN tests.
- [ ] Update `PROGRESS.md` with implemented behavior, verification evidence, remaining manual checks, and `尚未部署`.
- [ ] Commit as `docs: record product detail implementation`.

## Planned technical-debt cleanup

1. Remove three parallel modal state machines from `ProductsTab` after route integration.
2. Move the 400+ lines of inline create/edit form logic into one shared component used by list and detail.
3. Replace pagination-dependent product deep links with a direct ID endpoint and stable route.
4. Preserve old deep links through one compatibility mapper instead of continuing two navigation models.
5. Keep unrelated debt, especially passlib/bcrypt and image reordering, outside this change.
