# Temporary Product Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在独立新数据库上交付一个临时的、结构驱动的产品资料与采购／卖价区间联合导入流程，并以数据库式产品列表和期间明细供用户验收。

**Architecture:** 新增 0037 迁移和隔离的 `domains/product_imports/` 边界，不改变旧 `v3_upload_batches` 的生产语义；动态字段目录生成双 Sheet 模板，上传后暂存、确定性校验、差异预览，再用一个事务写入产品、扩展值、价格区间和审计记录。临时前后端使用显式开关和数据库名启动保护，只允许连接 `cruise_v3_clean`。

**Tech Stack:** FastAPI、Pydantic、SQLAlchemy、Alembic、PostgreSQL 17、openpyxl、pytest；Next.js 16、React 19、TypeScript、Vitest、Testing Library；Cloud SQL、Cloud Run、Vercel。

**Spec:** [临时产品资料与价格区间导入设计](../specs/2026-10-07-temporary-product-import-design.md)

## Global Constraints

- 生产数据库、正式 Cloud Run、Oracle Job 和正式 Vercel 域名不得连接新库或接收本轮迁移。
- 产品身份为标准化后的“产品代码 + 港口”；国家保留但不参与唯一判断。
- 产品资料、扩展字段、价格区间、审计和批次状态必须在一个事务中写入或全部回滚。
- 一个工作簿固定为 `产品资料` 和 `价格记录` 两个业务 Sheet；空 Sheet 表示不处理，不表示删除。
- 结构变化后旧模板可以读取错误，但不能提交；不得猜测已删除、改名或重名字段。
- 业务分类是产品系统表的 `single_select` 扩展字段，不增加 `products` 物理列。
- 完全相同价格边界但不同金额／币种为更新；部分重叠为阻止。
- 第一版有任何阻止项就阻止整批，不提供忽略错误行或部分提交。
- 空白单元格表示保留原值；清空使用明确的 `__CLEAR__` 标记。
- 使用 TDD；每个任务先看到针对性测试按预期失败，再写最小实现并验证通过。
- 稳定候选只运行一次后端完整回归、一次前端完整测试与构建；不得每个任务重复完整回归。

## Review Focus

- 同一港口的 ` abc `、`ABC` 必须被识别为同一产品，同代码不同港口允许存在；Task 1 和 Task 4 覆盖。
- 下载模板后管理员改动字段配置，旧模板上传应定位结构版本过期且零业务写入；Task 3、4 和 Task 6 覆盖。
- 同一文件中的重复产品、文件内价格重叠、与数据库价格重叠必须分别指出双方 Sheet／行／期间；Task 4 覆盖。
- 用户重复点击提交或第一次响应丢失后重试，只能得到原提交结果，不能重复创建产品或价格；Task 5 和 Task 6 覆盖。
- 预览后产品、扩展字段、选择项或价格区间被其他人修改，提交必须阻止并完整回滚；Task 5 覆盖。

---

## File Structure

所有路径相对 `curise_agent/`。

### 后端

- Create: `v3_backend/migrations/versions/0037_temporary_product_import.py` — 产品身份索引和导入批次／行／变更表。
- Create: `v3_backend/domains/product_imports/models.py` — 0037 ORM 模型。
- Create: `v3_backend/domains/product_imports/contracts.py` — 模板、批次、行、问题、预览和提交 DTO。
- Create: `v3_backend/domains/product_imports/catalog.py` — 产品核心字段和动态扩展字段的稳定 manifest。
- Create: `v3_backend/domains/product_imports/template.py` — 双 Sheet 工作簿生成。
- Create: `v3_backend/domains/product_imports/parser.py` — 工作簿读取和原始暂存。
- Create: `v3_backend/domains/product_imports/validation.py` — 值规范化、跨 Sheet、主数据和重叠校验。
- Create: `v3_backend/domains/product_imports/diff.py` — 新增／更新／跳过／警告／阻止差异。
- Create: `v3_backend/domains/product_imports/commit.py` — 加锁、原子提交、幂等和回滚。
- Create: `v3_backend/domains/product_imports/service.py` — HTTP 使用的稳定门面。
- Create: `v3_backend/apps/http/database_setup.py` — 临时状态、模板、批次和产品读取 API。
- Create: `v3_backend/scripts/seed_clean_product_fields.py` — 业务分类固定 UUID 幂等配置。
- Modify: `v3_backend/domains/masterdata/models.py`、`infrastructure/db/model_registry.py` — 身份索引和模型注册。
- Modify: `v3_backend/domains/dynamic_data/structures.py` — 同一表启用字段名称不能重复。
- Modify: `v3_backend/infrastructure/config.py`、`apps/http/startup.py`、`main.py` — 临时模块与数据库名门禁。
- Modify: `v3_backend/scripts/{bootstrap_fresh_database,audit_fresh_database}.py` — 0037 新空库基线与允许的固定配置。

### 前端

- Create: `v3-frontend/src/lib/database-setup-api.ts` — 临时 API 类型和调用。
- Create: `v3-frontend/src/app/dashboard/workbench/database-setup/page.tsx` — 准备、模板、上传、检查、核对、确认流程。
- Create: `v3-frontend/src/app/dashboard/workbench/database-setup/product-table.tsx` — 数据库式产品列表。
- Create: `v3-frontend/src/app/dashboard/workbench/database-setup/products/[productId]/page.tsx` — 产品资料及两类价格期间表格。
- Modify: `v3-frontend/src/lib/workbench-modules.ts`、`app/dashboard/workbench/page.tsx` — 仅临时环境显示入口。

### 测试与文档

- Create: `v3_backend/test_v2/integration/product_imports/` — 迁移、模板、解析、校验、提交、API 和 PostgreSQL 测试。
- Create: `v3_backend/test_v2/e2e/test_temporary_product_import.py` — 下载至回滚完整路径。
- Create: `v3-frontend/src/app/dashboard/workbench/database-setup/*.test.tsx`、`src/lib/database-setup-api.test.ts`。
- Create: `docs/temporary-product-import-2026-10-07/{PROGRESS,VERIFICATION,RELEASE_PLAN}.md`。

## Task 1: Install the Database Foundation

**Files:**
- Create: `v3_backend/migrations/versions/0037_temporary_product_import.py`
- Create: `v3_backend/domains/product_imports/{__init__,models}.py`
- Modify: `v3_backend/domains/masterdata/models.py`
- Modify: `v3_backend/infrastructure/db/model_registry.py`
- Test: `v3_backend/test_v2/integration/product_imports/{test_models,test_migration,test_postgres}.py`

**Interfaces:**
- Produces: `ImportBatch`, `ImportRow`, `ImportChange`; PostgreSQL unique index `uq_products_active_normalized_code_port`; Alembic head `0037_temporary_product_import`.
- Consumed by: Tasks 2–6.

- [ ] **Step 1: Write failing model and migration tests**

  Add tests proving 0036→0037 preserves all existing rows; creates the three import tables and indexes; same active port rejects ` abc ` after `ABC`; different ports accept the same code; inactive duplicates are accepted but conflicting reactivation fails. Assert `ProductPricePeriod.revision` starts at 1, import row identity is unique by `(batch_id, sheet_key, source_row_number)`, and batch status accepts only the declared lifecycle.

- [ ] **Step 2: Run focused tests and verify RED**

  Run: `cd v3_backend && .venv/bin/python -m pytest test_v2/integration/product_imports/test_models.py test_v2/integration/product_imports/test_migration.py test_v2/integration/product_imports/test_postgres.py -q`
  
  Expected: FAIL because revision 0037 and `domains.product_imports.models` do not exist.

- [ ] **Step 3: Implement the minimal schema**

  Add a PostgreSQL partial expression unique index over `lower(btrim(code)), port_id WHERE status AND code IS NOT NULL AND port_id IS NOT NULL`, plus a non-null `revision` column on price periods. Define import models with JSONB-compatible payloads, optimistic snapshot fields, immutable file hash, per-row issues/action, counters, actor IDs and timestamps; no FK from staged identity to a not-yet-created product.

- [ ] **Step 4: Verify GREEN and migration round-trip**

  Run the focused command, `alembic heads`, and Ruff for changed Python files. Expected: all tests pass, exactly one head `0037_temporary_product_import`, upgrade/downgrade/upgrade succeeds on isolated PostgreSQL.

- [ ] **Step 5: Commit**

  Commit: `feat: add temporary product import schema`.

## Task 2: Seed the Product Business Classification

**Files:**
- Create: `v3_backend/scripts/seed_clean_product_fields.py`
- Modify: `v3_backend/scripts/{bootstrap_fresh_database,audit_fresh_database}.py`
- Modify: `v3_backend/domains/dynamic_data/structures.py`
- Test: `v3_backend/test_v2/integration/product_imports/test_clean_field_seed.py`
- Test: `v3_backend/test_v2/integration/dynamic_data/test_structures.py`
- Test: `v3_backend/test_v2/section_1_security/test_fresh_database_bootstrap.py`

**Interfaces:**
- Consumes: products system table UUID and `DataField` validation from 0036.
- Produces: `seed_product_business_classification(db, actor_id) -> DataField`; fixed field/option UUIDs and exact four labels.

- [ ] **Step 1: Write failing idempotency and audit tests**

  Assert an empty 0037 database gains exactly one active `single_select` field named“业务分类”, default“未分类”, options“中标产品／未中标产品／临时产品／未分类”; a second run produces no duplicate or schema-version bump. Assert mismatched existing fixed UUID content is rejected rather than overwritten, two active fields in one table cannot have the same trimmed case-insensitive label, and fresh-database audit accepts only this exact configuration with zero business rows.

- [ ] **Step 2: Run tests and verify RED**

  Run: `cd v3_backend && .venv/bin/python -m pytest test_v2/integration/product_imports/test_clean_field_seed.py test_v2/section_1_security/test_fresh_database_bootstrap.py -q`
  
  Expected: FAIL because seed and 0037 audit contract are absent.

- [ ] **Step 3: Implement seed and bootstrap integration**

  Use fixed UUID constants, the existing field-definition validator and one transaction. Add structure-layer duplicate-label protection for both new and renamed active fields; fresh bootstrap invokes the same seed after system-table rows exist, and audit validates label, type, default, option UUID/label/active states while confirming `v3_data_records` remains empty.

- [ ] **Step 4: Verify GREEN**

  Run the focused tests and Ruff. Expected: pass with no second-run mutation.

- [ ] **Step 5: Commit**

  Commit: `feat: seed product business classification`.

## Task 3: Generate a Schema-Versioned Two-Sheet Template

**Files:**
- Create: `v3_backend/domains/product_imports/{contracts,catalog,template}.py`
- Test: `v3_backend/test_v2/integration/product_imports/test_template.py`

**Interfaces:**
- Consumes: Task 2 product system table and extension fields.
- Produces: `build_product_workbook(db, actor, *, include_existing: bool, product_ids: list[int] | None = None) -> bytes`; workbook manifest with `contract_version=1` and product `schema_version`.
- Consumed by: Tasks 4, 6 and 7.

- [ ] **Step 1: Write failing workbook behavior tests**

  Reload generated bytes with openpyxl and assert exact Sheet names `产品资料`, `价格记录`, hidden `_系统信息`; core plus active extension columns; text formatting for code; date/number formats; visible Chinese guidance; dropdowns for configured master data and finite choices; hidden IDs/versions for existing rows; chronological one-row-per-period export. Currency choices come only from distinct codes already configured in exchange-rate/product/price data; if none exist, the cell accepts a validated three-letter uppercase code instead of inventing a currency list. Add a test that an added extension field appears in the next workbook while an archived field does not.

- [ ] **Step 2: Run tests and verify RED**

  Run: `cd v3_backend && .venv/bin/python -m pytest test_v2/integration/product_imports/test_template.py -q`
  
  Expected: FAIL because `build_product_workbook` is missing.

- [ ] **Step 3: Implement catalog and workbook generation**

  Define immutable core-field keys and a manifest of extension field UUID, label, type, requirement and allowed choices. Write two business Sheets and a hidden manifest Sheet; lock header cells and Sheet names, keep data cells editable, and never embed secret IDs except record/version identifiers needed for optimistic updates.

- [ ] **Step 4: Verify GREEN**

  Run the focused tests and Ruff. Expected: generated workbook round-trips and manifest values match literal assertions.

- [ ] **Step 5: Commit**

  Commit: `feat: generate configured product import workbook`.

## Task 4: Parse, Stage and Validate Both Sheets

**Files:**
- Create: `v3_backend/domains/product_imports/{parser,validation,diff}.py`
- Test: `v3_backend/test_v2/integration/product_imports/{test_parser,test_validation,test_diff}.py`

**Interfaces:**
- Consumes: Task 1 staging models and Task 3 manifest.
- Produces: `parse_workbook(db, file_bytes, filename, user_id) -> ImportBatch`; `validate_batch(db, batch_id, user_id) -> BatchPreview`; normalized identity `normalize_product_key(code, port_id) -> tuple[str,int]`.
- Consumed by: Tasks 5–7.

- [ ] **Step 1: Write failing parser tests**

  Assert missing/renamed Sheet, duplicate/unknown header, stale contract/schema, formula cell, oversized file and malformed workbook become Chinese batch issues with Sheet/row/field. Assert empty business Sheet is allowed and original source coordinates are retained.

- [ ] **Step 2: Verify parser RED, implement minimal parser, then GREEN**

  Run: `cd v3_backend && .venv/bin/python -m pytest test_v2/integration/product_imports/test_parser.py -q`; expect missing parser failure. Implement bounded read-only parsing, formula rejection, manifest verification and raw staging, then rerun to pass.

- [ ] **Step 3: Write failing validation and diff tests**

  Cover case/whitespace product identity, same code/different port, conflicting duplicate product rows, missing master references, unknown/inactive select choice, `__CLEAR__`, price-row reference to new same-batch product, missing product, exact-period skip/update, within-file overlap, database overlap, missing purchase/selling warning, and literal create/update/skip/warn/block counts. Conflicts must cite both involved source rows or the existing database period.

- [ ] **Step 4: Verify validation RED, implement minimal validation/diff, then GREEN**

  Run validation and diff test files; expect missing functions. Implement deterministic maps and interval comparison without LLM or fuzzy matching, persist normalized values and snapshot versions, then rerun all Task 4 tests to pass.

- [ ] **Step 5: Commit**

  Commit: `feat: validate staged product imports`.

## Task 5: Commit and Roll Back Atomically

**Files:**
- Create: `v3_backend/domains/product_imports/commit.py`
- Modify: `v3_backend/domains/product_imports/service.py`
- Modify: `v3_backend/domains/masterdata/price_periods.py`
- Test: `v3_backend/test_v2/integration/product_imports/{test_commit,test_concurrency,test_rollback}.py`

**Interfaces:**
- Consumes: Task 4 ready batches and snapshots.
- Produces: `commit_batch(db, batch_id, user_id) -> CommitResult`; `rollback_batch(db, batch_id, user_id) -> RollbackResult`; transaction-scoped price-period write helpers that do not call `commit()`.
- Consumed by: Task 6.

- [ ] **Step 1: Write failing atomicity and idempotency tests**

  Assert one mixed batch creates/updates core product fields, extension anchor/value and purchase/selling periods with one audit trail. Force a late price failure and assert zero product, extension, price, change or completed-state writes; repeat the same successful commit and assert identical result/counts with no duplicate rows.

- [ ] **Step 2: Run tests and verify RED**

  Run: `cd v3_backend && .venv/bin/python -m pytest test_v2/integration/product_imports/test_commit.py -q`
  
  Expected: FAIL because the commit coordinator is missing.

- [ ] **Step 3: Implement minimal atomic coordinator**

  Lock batch, schema row and affected products in stable order; revalidate file hash, schema version, product revisions, extension revisions and period snapshots. Use flush-only internal writers and one outer transaction; record every create/update in `ImportChange`; return the stored result for already committed batches.

- [ ] **Step 4: Write and run concurrency/rollback RED tests**

  On real PostgreSQL, mutate product, select option and price period after preview and assert each prevents all writes. Assert every price-period create/update/deactivate increments `revision`; rollback refuses when later revisions depend on imported data, otherwise it restores before-values and archives/deletes rows created solely by the batch exactly once.

- [ ] **Step 5: Implement concurrency guards and rollback, then verify GREEN**

  Run all Task 5 tests and relevant existing price-period tests. Expected: all pass; existing product HTTP create/update behavior remains unchanged.

- [ ] **Step 6: Commit**

  Commit: `feat: commit product imports atomically`.

## Task 6: Expose a Database-Safe Temporary API

**Files:**
- Create: `v3_backend/apps/http/database_setup.py`
- Modify: `v3_backend/domains/product_imports/service.py`
- Modify: `v3_backend/infrastructure/config.py`
- Modify: `v3_backend/apps/http/startup.py`
- Modify: `v3_backend/main.py`
- Test: `v3_backend/test_v2/integration/product_imports/test_api.py`
- Test: `v3_backend/test_v2/section_1_security/test_database_setup_gate.py`

**Interfaces:**
- Produces: `/api/database-setup/status`, `/product-template`, `/imports`, `/imports/{id}/validate`, `/imports/{id}/rows`, `/imports/{id}/commit`, `/imports/{id}/rollback`, `/products`, `/products/{id}`.
- Consumed by: Task 7.

- [ ] **Step 1: Write failing authorization, gate and API flow tests**

  Assert disabled mode is 503; non-admin is 403; enabled mode refuses startup unless the connected database name exactly matches `TEMP_DATABASE_SETUP_EXPECTED_DATABASE=cruise_v3_clean`; no request parameter can select another database. Exercise upload → validate → rows → commit → product detail and response-loss retry; stale template remains readable but `can_commit=false`.

- [ ] **Step 2: Run tests and verify RED**

  Run: `cd v3_backend && .venv/bin/python -m pytest test_v2/integration/product_imports/test_api.py test_v2/section_1_security/test_database_setup_gate.py -q`
  
  Expected: FAIL because routes and configuration are absent.

- [ ] **Step 3: Implement routes and startup protection**

  Add `TEMP_DATABASE_SETUP_ENABLED=false` and exact expected database setting. Reuse administrator dependency, size limits and safe error envelopes; return downloadable workbook bytes and paginated row/product responses without exposing DB URLs or dynamic-field UUIDs as editable user values.

- [ ] **Step 4: Verify GREEN and existing API isolation**

  Run Task 6 tests plus auth/CORS/startup tests and inspect OpenAPI for route/auth contracts. Expected: all pass; old `/api/data-upload` contracts unchanged.

- [ ] **Step 5: Commit**

  Commit: `feat: expose temporary database setup API`.

## Task 7: Build the Temporary Five-Stage UI and Product Views

**Files:**
- Create: `v3-frontend/src/lib/database-setup-api.ts`
- Create: `v3-frontend/src/lib/database-setup-api.test.ts`
- Create: `v3-frontend/src/app/dashboard/workbench/database-setup/{page,product-table}.tsx`
- Create: `v3-frontend/src/app/dashboard/workbench/database-setup/{page,product-table}.test.tsx`
- Create: `v3-frontend/src/app/dashboard/workbench/database-setup/products/[productId]/{page,page.test}.tsx`
- Modify: `v3-frontend/src/lib/workbench-modules.ts`
- Modify: `v3-frontend/src/app/dashboard/workbench/page.tsx`

**Interfaces:**
- Consumes: Task 6 HTTP responses only.
- Produces: admin journey“准备 → 下载模板 → 上传检查 → 核对 → 确认结果”, product table and chronological price-detail page.

- [ ] **Step 1: Write failing API and route component tests**

  Assert exact URL/method/body contracts and uncertain-submit result check. Render each stage with realistic full payloads; verify disabled/incorrect-database state has no upload action, blockers disable confirmation, issues show Sheet/row/field/reason, and changed values render old-left/new-right.

- [ ] **Step 2: Run tests and verify RED**

  Run: `cd v3-frontend && pnpm test -- src/lib/database-setup-api.test.ts src/app/dashboard/workbench/database-setup/page.test.tsx src/app/dashboard/workbench/database-setup/product-table.test.tsx 'src/app/dashboard/workbench/database-setup/products/[productId]/page.test.tsx'`
  
  Expected: FAIL because modules/routes do not exist.

- [ ] **Step 3: Implement the minimal temporary workflow UI**

  Follow current compact workbench components and typography. The preparation state shows the exact new-database name, schema version and counts plus a“配置产品字段”link to the existing product data-table settings; keep one primary action per step, preserve selected file and errors, show batch counts, support paginated issues/changes, and expose rollback only after a committed batch.

- [ ] **Step 4: Implement product list/detail behavior under failing tests**

  Product list is one row per product with code, port, name, supplier, unit, commodity category, brand, business classification and status. Detail shows core/extension information plus separate purchase/selling tables sorted by start date ascending and derived“已结束／当前／未来”states.

- [ ] **Step 5: Verify GREEN, typecheck and build**

  Run focused tests, `pnpm exec tsc --noEmit`, then `pnpm build`. Expected: zero failures and successful production build.

- [ ] **Step 6: Commit**

  Commit: `feat: add temporary product import workbench`.

## Task 8: Prove the Full Local and Isolated-Database Journey

**Files:**
- Create: `v3_backend/test_v2/e2e/test_temporary_product_import.py`
- Create: `docs/temporary-product-import-2026-10-07/{PROGRESS,VERIFICATION,RELEASE_PLAN}.md`
- Modify: `/Users/yichuanzhang/Desktop/curise_system_2/PROGRESS.md`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces: reproducible verification evidence and exact deployment/rollback checklist.

- [ ] **Step 1: Write and run failing end-to-end test**

  The test creates required country/port/supplier/category data, downloads the real template, fills one new product with business classification plus three chronological purchase and selling periods, uploads, validates, previews, commits, reads list/detail, retries commit and rolls back. Before completing missing glue it must fail at the first incomplete user-visible boundary.

- [ ] **Step 2: Complete only missing glue and verify E2E GREEN**

  Run: `cd v3_backend && .venv/bin/python -m pytest test_v2/e2e/test_temporary_product_import.py -q`. Expected: pass with literal row counts and values.

- [ ] **Step 3: Run one stable-candidate regression**

  Backend: full pytest once, Ruff for changed Python, `git diff --check`. Frontend: full Vitest once, TypeScript, production build once. Record exact pass/skip/fail counts and elapsed time; do not rerun full suites for documentation-only edits.

- [ ] **Step 4: Update verification and progress documents**

  Record commits, migration head, test commands, known warnings, no-production-write evidence, new-database before/after counts, exact rollback target and remaining deployment steps.

- [ ] **Step 5: Commit**

  Commit: `test: verify temporary product import journey`.

## Task 9: Update the New Database and Deploy the Isolated Temporary Environment

**Files:**
- Modify after verification: `docs/temporary-product-import-2026-10-07/{PROGRESS,VERIFICATION,RELEASE_PLAN}.md`
- Modify after verification: `/Users/yichuanzhang/Desktop/curise_system_2/PROGRESS.md`

**Interfaces:**
- Consumes: stable Task 8 commit and Cloud SQL instance `cruise-v3-db-clean-20261006`.
- Produces: migrated/configured `cruise_v3_clean`, isolated backend/frontend URLs and production-isolation evidence.

- [ ] **Step 1: Capture read-only preflight evidence**

  Verify git SHA/clean worktree, new and production Cloud SQL instance state, new DB head 0036, expected empty business counts, production DB head/count fingerprint, current production Cloud Run/Vercel/Oracle Job identifiers and Secret Manager access. Abort on any unexplained drift.

- [ ] **Step 2: Back up and migrate only the new database**

  Create an on-demand backup of `cruise-v3-db-clean-20261006`; run 0036→0037 and the fixed business-classification seed through a local Cloud SQL Auth Proxy; run fresh-database audit plus direct SQL constraint checks. Verify production DB fingerprint remains identical.

- [ ] **Step 3: Deploy an isolated backend candidate**

  Build the verified SHA once, deploy a new Cloud Run service name with 0% public production traffic, new-DB secret only, `TEMP_DATABASE_SETUP_ENABLED=true`, expected DB name `cruise_v3_clean`, exact temporary frontend origin and no scheduler/Oracle Job wiring. Verify health, build metadata, auth, CORS, status, template and ERROR logs.

- [ ] **Step 4: Deploy an isolated frontend candidate**

  Create a separate Vercel environment/project or unpromoted protected deployment pointed only at the temporary backend; do not change the production alias. Verify login, field configuration link, template download, upload/check/review UI and read-only empty product list.

- [ ] **Step 5: Execute a reversible synthetic smoke test**

  Use clearly marked synthetic master data and one product, complete import and product-detail checks, then rollback and verify business tables return to their pre-smoke counts except audit history. Do not use or copy production business data.

- [ ] **Step 6: Record immutable deployment evidence**

  Save backend service/revision/image digest, frontend deployment ID/URL, database backup ID/head, secret names, synthetic batch ID, post-rollback counts, production isolation fingerprint and disable/delete procedure. Update progress documents without claiming production deployment.

- [ ] **Step 7: Commit documentation only**

  Commit: `docs: verify isolated product import environment`. Do not rebuild artifacts for this documentation-only commit.
