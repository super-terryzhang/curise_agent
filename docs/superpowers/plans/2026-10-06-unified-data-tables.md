# Unified Data Table Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把产品、供应商、订单和用户创建的表统一到“数据表管理”，允许为核心表新增并维护扩展字段，同时保护核心字段和既有业务流程。

**Architecture:** `v3_data_tables` 作为统一目录，三张核心表由固定适配器读取原 ORM 数据；用户新增字段继续保存在 `v3_data_fields`，扩展值以带 `source_record_id` 的 `v3_data_records` 锚点保存。统一 API 按 `table_kind` 分派到现有动态记录服务或系统表适配器，前端只呈现“数据表”，但明确显示锁定核心字段与可编辑扩展信息。

**Tech Stack:** FastAPI、Pydantic、SQLAlchemy、Alembic、PostgreSQL、pytest；Next.js、React、TypeScript、Vitest、Testing Library。

**Spec:** [统一数据表管理第一轮设计](../specs/2026-10-06-unified-data-tables-design.md)

## Global Constraints

- 页面只使用“数据表管理／数据表”，不要求用户理解“自定义／非自定义”。
- 产品、供应商、订单核心数据仍以 `products`、`suppliers`、`v2_orders` 为唯一来源，不复制或经统一 API 写回。
- 核心字段可查看但不能改类型、排序、归档或删除；核心内容通过原业务页面修改。
- 系统表扩展字段第一轮支持 text、number、date、datetime、single_select、multi_select、boolean，不支持 link。
- 用户创建表现有八类型、表间关联、筛选、归档恢复和历史行为必须保持。
- 系统表第一轮不支持与其他表建立关联，不支持扩展值批量更新、导入导出和公式。
- 系统记录不在统一页面归档或恢复；核心业务状态作为只读字段展示。
- 原业务读取范围必须保留，尤其不能通过统一订单表读取其他账号无权查看的订单或历史。
- 每个小任务只运行针对性测试；稳定候选运行一次本地完整回归。此阶段只交付本地验收，不合并 main、不推送、不迁移生产、不部署。
- 所有写入使用合成测试数据或隔离 PostgreSQL；不得用生产产品、供应商或订单做自动写入验收。

## Review Focus

- 系统记录第一次保存成功但响应丢失：相同 `request_id` 重试只返回已有结果，不产生第二锚点或第二创建历史；无关记录已占用该 ID 时明确返回冲突；Task 3。
- 核心订单的可见范围：列表、详情、扩展值和记录历史必须采用同一 Actor 范围，不能只保护列表；Task 2、3、4。
- 系统表结构保护：伪造核心字段 UUID、系统表改名／归档、link 扩展字段都在服务端拒绝，而不只依赖按钮隐藏；Task 3、4。
- 0035 已有用户表和记录升级 0036：全部值、关联、唯一占位和历史保持，三张系统目录幂等且不修改核心业务行；Task 1。
- 没有扩展字段或没有扩展值锚点：系统记录仍可分页查看，不产生读取时写入和逐行查询；核心记录删除后其遗留锚点也不得被列表、详情或历史接口暴露；Task 2、3、6。

---

## File Structure

所有路径相对 `curise_agent/`。

### 后端

- Create: `v3_backend/migrations/versions/0036_unified_data_tables.py` — 目录类型、来源记录锚点及三张系统目录种子。
- Create: `v3_backend/domains/dynamic_data/system_tables.py` — 固定注册表、核心字段描述和产品／供应商／订单只读适配器。
- Create: `v3_backend/domains/dynamic_data/system_records.py` — 系统记录与扩展值的合并读取、首次保存、版本和历史。
- Modify: `v3_backend/domains/dynamic_data/{models,schemas,query,records,structures,lifecycle,history,service}.py` — 统一目录契约、分派和保护。
- Modify: `v3_backend/apps/http/{data_tables,startup}.py`、`v3_backend/infrastructure/config.py` — 字符串记录标识、统一接口和 0036 发布门禁。
- Create: `v3_backend/test_v2/integration/dynamic_data/{test_system_tables,test_system_records}.py`.
- Modify: `v3_backend/test_v2/integration/dynamic_data/{test_api,test_lifecycle,test_migration,test_models,test_postgres,test_query,test_records,test_structures}.py`.
- Modify: `v3_backend/test_v2/e2e/test_custom_data_tables.py` and `v3_backend/test_v2/section_1_security/test_data_tables_release.py`.

### 前端

- Modify: `v3-frontend/src/lib/{data-tables-types,data-tables-api,data-tables-view}.ts` — 系统表与核心字段响应类型。
- Modify: `v3-frontend/src/components/settings/data-tables/{table-list,table-detail,fields-panel,field-editor,records-panel,record-editor,history-panel}.tsx` — 统一文案和系统表交互。
- Modify: `v3-frontend/src/components/settings/data-tables/{table-list,fields-panel,field-editor,records-panel,record-editor,history-panel}.test.tsx`.
- Modify: `v3-frontend/src/lib/{data-tables-api,data-tables-view}.test.ts` and `v3-frontend/src/test/data-tables-fixtures.ts`.
- Create: `v3-frontend/src/components/settings/data-tables/unified-data-tables-flow.test.tsx` — 统一列表、系统扩展字段和普通用户表的完整组件流程。
- Modify: `v3-frontend/src/app/dashboard/settings/page.tsx` — 设置入口统一名称。

### Documentation

- Modify: `docs/custom-data-tables-2026-10-06/{PROGRESS,VERIFICATION,RELEASE_PLAN}.md`.
- Modify at final handoff: `/Users/yichuanzhang/Desktop/curise_system_2/PROGRESS.md`.

## Public Interfaces

- `TableResponse.table_kind: Literal["user","system"]`
- `TableResponse.system_key: Literal["products","suppliers","orders"] | None`
- `FieldResponse.source: Literal["core","extension"]`
- `FieldResponse.locked: bool`
- `FieldResponse.system_key: str | None`
- `RecordResponse.id: str` — user 表为 UUID 字符串，system 表为原主键字符串。
- `RecordResponse.business_url: str | None`
- `RecordResponse.revision: int` — system 记录没有扩展锚点时为 `0`；user 记录保持从 `1` 开始。
- `RecordResponse.created_by/updated_by: int | None` — system 记录没有扩展锚点时为 `None`，不伪造操作人。
- `RecordQuery.q: str | None` — 仅 system 表使用，只搜索适配器声明的核心文本字段；user 表继续使用现有字段筛选。
- `ChangeResponse.entity_id/record_id: str | None`、`ChangeQuery.record_id: str | None`、`ErrorIssue.record_id: str | None` — system 表对外使用来源主键，内部历史仍引用锚点 UUID。
- `SystemRecordUpdate(request_id:UUID, source_record_id:str, expected_revision:int>=0, schema_version:int>=1, values:dict)`
- `SystemField` — 固定 UUID、label、field_type、system_key、读取映射；不持久化为 `DataField`。
- `SystemTableAdapter.list_records(db, actor, *, q, page, page_size) -> SystemRecordPage`
- `SystemTableAdapter.get_record(db, actor, source_record_id) -> SystemRecord`
- `save_system_record(db, table_id, body:SystemRecordUpdate, *, actor) -> RecordResponse`
- `list_tables(db, *, actor, status, page, page_size) -> Page[TableResponse]` — system 表计数遵守 Actor 可见范围；user 表保持公司共享计数。

## Task 1: Upgrade the Unified Catalog Schema

**Files:**
- Create: `v3_backend/migrations/versions/0036_unified_data_tables.py`
- Modify: `v3_backend/domains/dynamic_data/models.py`
- Modify: `v3_backend/domains/dynamic_data/schemas.py`
- Test: `v3_backend/test_v2/integration/dynamic_data/test_migration.py`
- Test: `v3_backend/test_v2/integration/dynamic_data/test_models.py`

**Interfaces:** Produces `DataTable.table_kind/system_key`, `DataRecord.source_record_id`, the three fixed table UUIDs, and response contract fields consumed by all later tasks.

- [ ] **Step 1: Write failing migration and model tests**

  Add `test_0035_to_0036_preserves_user_tables_and_seeds_system_catalog` asserting existing 0035 tables, fields, records, links, unique values and history are byte-for-byte preserved; exactly `products`, `suppliers`, `orders` are added; product/supplier/order row counts and sentinel hashes do not change. Add model constraint tests for duplicate `system_key` and duplicate `(table_id, source_record_id)`.

- [ ] **Step 2: Run the focused tests and verify RED**

  Run from `v3_backend`: `.venv/bin/python -m pytest test_v2/integration/dynamic_data/test_models.py test_v2/integration/dynamic_data/test_migration.py -q`

  Expected: FAIL because 0036 and the new columns do not exist.

- [ ] **Step 3: Implement the migration and contracts**

  Add `table_kind` with allowed values `user/system`, nullable unique `system_key`, and nullable `source_record_id` plus composite unique constraint. Seed three fixed UUID rows with actor `0` and explicit UTC timestamps; upgrade is idempotent at the data level. Persisted `DataField` rows serialize explicitly as `source=extension, locked=false`; extend schemas with the public interfaces above and keep old response parsing compatible through explicit defaults in tests only, not runtime guesses.

- [ ] **Step 4: Verify GREEN and migration head**

  Run the focused tests plus `.venv/bin/python -m alembic heads`; expect all tests PASS and exactly `0036_unified_data_tables` as head. Run `.venv/bin/python -m ruff check domains/dynamic_data/models.py domains/dynamic_data/schemas.py migrations/versions/0036_unified_data_tables.py`.

- [ ] **Step 5: Commit**

  Commit message: `feat: add unified data table catalog schema`.

## Task 2: Add Read-Only Core Table Adapters

**Files:**
- Create: `v3_backend/domains/dynamic_data/system_tables.py`
- Modify: `v3_backend/domains/dynamic_data/query.py`
- Modify: `v3_backend/domains/dynamic_data/service.py`
- Test: `v3_backend/test_v2/integration/dynamic_data/test_system_tables.py`
- Test: `v3_backend/test_v2/integration/dynamic_data/test_query.py`

**Interfaces:** Produces `SYSTEM_TABLES`, `system_fields(table)`, adapter list/get/count/search and `business_url`; Task 3 consumes adapter existence and visibility checks.

- [ ] **Step 1: Write failing adapter tests**

  Add `test_catalog_lists_system_and_user_tables_without_type_column_semantics`, `test_product_supplier_order_adapters_read_declared_fields`, `test_system_page_batches_relation_labels`, and `test_order_adapter_reuses_actor_visibility_for_list_and_detail`. Assert system tables appear only in the active catalog, field/record counts are Actor-scoped, search parameters remain bound, a 50-row page stays within a fixed query budget, and reading a record without an extension anchor performs no write.

- [ ] **Step 2: Run focused tests and verify RED**

  Run: `.venv/bin/python -m pytest test_v2/integration/dynamic_data/test_system_tables.py test_v2/integration/dynamic_data/test_query.py -q`

  Expected: FAIL because the registry and dispatch do not exist.

- [ ] **Step 3: Implement adapters and read dispatch**

  Define fixed core field UUIDs and seven supported display types. Product, supplier and order adapters use ORM column allowlists, positive-integer source IDs, actor-aware queries, stable pagination, batched relation names and exact business URLs. `q` searches only declared core text fields; system tables reject dynamic-field filter/sort inputs in this release. Extend table/field/record list and detail queries to merge extension values by one batched `source_record_id` query; reads never create `DataRecord` rows. Unanchored responses use revision `0`, nullable actors and core timestamps; the envelope stays active while the real business status is a locked core field.

- [ ] **Step 4: Verify GREEN and existing user-table reads**

  Run focused tests plus `.venv/bin/python -m pytest test_v2/integration/dynamic_data/test_query.py test_v2/integration/dynamic_data/test_lifecycle.py -q`. Assert existing user-table numeric sort, links and archive reads remain unchanged.

- [ ] **Step 5: Commit**

  Commit message: `feat: expose core records through data table adapters`.

## Task 3: Protect Core Structure and Save Extension Values

**Files:**
- Create: `v3_backend/domains/dynamic_data/system_records.py`
- Modify: `v3_backend/domains/dynamic_data/{records,structures,lifecycle,history,service}.py`
- Test: `v3_backend/test_v2/integration/dynamic_data/test_system_records.py`
- Test: `v3_backend/test_v2/integration/dynamic_data/test_structures.py`
- Test: `v3_backend/test_v2/integration/dynamic_data/test_postgres.py`

**Interfaces:** Produces `save_system_record(...)`; enforces system-table structural locks and non-link extension fields. Task 4 exposes it through HTTP.

- [ ] **Step 1: Write failing protection and save tests**

  Add tests proving system tables cannot rename/archive, core fields cannot update/reorder/archive, system-table reordering accepts exactly its active extension-field IDs, system-table extension fields reject `link`, and user-table link fields cannot target a system table. Add first-save tests with revision `0`, later exact revision updates, stale revision conflicts, deleted/invisible source rejection, unique constraints, validation rollback and actor-scoped history. After a core row is deleted, its old anchor must be invisible through list, detail and history.

- [ ] **Step 2: Add retry and concurrency RED tests**

  In PostgreSQL tests, run two first saves against one `(table_id, source_record_id)`: only one distinct request wins. Repeating the winning `request_id` and normalized body returns one anchor and one create history; reusing it with changed content, another Actor, another source record, or an ID already used by an unrelated record returns 409. Verify a source deletion committed before save is rejected and a deletion committed after save makes the anchor unreachable through public reads.

- [ ] **Step 3: Run focused tests and verify RED**

  Run SQLite tests first, then the existing isolated PostgreSQL command for `test_postgres.py`; expected failures identify the missing system save path, not infrastructure errors.

- [ ] **Step 4: Implement atomic system extension writes**

  Validate adapter visibility and existence before and after locks; lock the system table structure, source row and then the extension anchor in one documented order. First save inserts `DataRecord(id=request_id, source_record_id=...)`; unique conflict reloads and compares original normalized creation request, source and actor. Persist only extension field UUIDs, preserve unique/history transaction behavior, and map history record filters and responses through the source ID without exposing the internal anchor UUID. Public reads always begin from an Actor-visible core row, so detached anchors remain inaccessible.

- [ ] **Step 5: Verify GREEN and user-table write regression**

  Run `test_system_records.py`, `test_structures.py`, `test_records.py`, `test_lifecycle.py` and PostgreSQL focused tests. Run Ruff on touched files.

- [ ] **Step 6: Commit**

  Commit message: `feat: save protected core table extensions`.

## Task 4: Extend the HTTP Contract and Release Gate

**Files:**
- Modify: `v3_backend/apps/http/data_tables.py`
- Modify: `v3_backend/apps/http/startup.py`
- Modify: `v3_backend/infrastructure/config.py`
- Modify: `v3_backend/test_v2/integration/dynamic_data/test_api.py`
- Modify: `v3_backend/test_v2/section_1_security/test_data_tables_release.py`

**Interfaces:** Record route IDs become validated strings and dispatch by table kind; system record PATCH accepts `SystemRecordUpdate`. Final schema head is 0036; the controlled unpublished transition accepts only the explicitly tested 0034/0035/0036 sequence while the module is disabled.

- [ ] **Step 1: Write failing API and startup matrix tests**

  Test unified responses, core write rejection, path/body source-ID mismatch, malformed or injected source IDs, order visibility, first-save retry and Chinese issues. Assert the disabled transition accepts only current 0034、0035 or 0036 on the single Alembic lineage whose expected head is 0036, and rejects multiple/divergent/unknown heads, enabled bridge mode and any future head. Assert 503 and validation messages no longer expose “自定义数据表” terminology.

- [ ] **Step 2: Run tests and verify RED**

  Run: `.venv/bin/python -m pytest test_v2/integration/dynamic_data/test_api.py test_v2/section_1_security/test_data_tables_release.py -q`

- [ ] **Step 3: Implement routing and exact release transition**

  Parse `record_id` only after loading the table; user tables require UUID, system tables pass the adapter allowlisted source parser. Keep `CUSTOM_DATA_TABLES_ENABLED` internal. Rename the transition value to `unified_data_tables_0034_0036` and allow only expected head 0036 with current 0034, 0035 or 0036 while the module is disabled; normal mode remains exact head equality.

- [ ] **Step 4: Verify GREEN, OpenAPI and old endpoints**

  Run focused tests, architecture check and assert existing product, supplier and order APIs still return their established contracts in regression fixtures.

- [ ] **Step 5: Commit**

  Commit message: `feat: expose unified data table API safely`.

## Task 5: Unify the Data Table List and Field Experience

**Files:**
- Modify: `v3-frontend/src/lib/{data-tables-types,data-tables-api,data-tables-view}.ts`
- Modify: `v3-frontend/src/components/settings/data-tables/{table-list,table-detail,fields-panel,field-editor}.tsx`
- Modify: `v3-frontend/src/app/dashboard/settings/page.tsx`
- Modify: `v3-frontend/src/components/settings/data-tables/{table-list,fields-panel,field-editor}.test.tsx`
- Modify: `v3-frontend/src/lib/{data-tables-api,data-tables-view}.test.ts` and `v3-frontend/src/test/data-tables-fixtures.ts`

**Interfaces:** Frontend consumes `table_kind`, `system_key`, `source` and `locked`; existing user-table calls keep the same functions.

- [ ] **Step 1: Write failing UI tests**

  Assert the page title is “数据表管理”, the list contains 产品／供应商／订单 plus a user table, no user-facing “自定义数据表” copy exists, system rows have only “打开”, and “新建数据表” still works. Assert core fields show a lock state without edit/reorder/archive controls; extension fields retain them; system field type selector omits “关联记录”.

- [ ] **Step 2: Run component tests and verify RED**

  Run: `pnpm test -- src/components/settings/data-tables/table-list.test.tsx src/components/settings/data-tables/fields-panel.test.tsx src/components/settings/data-tables/field-editor.test.tsx src/lib/data-tables-api.test.ts`

- [ ] **Step 3: Implement the unified list and field UI**

  Replace user-facing custom terminology, branch actions from capabilities rather than table name, render locked core field rows consistently, and keep the approved “当前类型” read-only treatment. Preserve the existing “最多可输入 × 个字符” design.

- [ ] **Step 4: Verify GREEN and TypeScript**

  Run the focused tests and `pnpm exec tsc --noEmit`.

- [ ] **Step 5: Commit**

  Commit message: `feat: unify data table catalog and fields UI`.

## Task 6: Build the Core/Extension Record Editing Experience

**Files:**
- Modify: `v3-frontend/src/components/settings/data-tables/{records-panel,record-editor,history-panel,table-detail}.tsx`
- Modify: `v3-frontend/src/components/settings/data-tables/{records-panel,record-editor,history-panel}.test.tsx`
- Modify: `v3-frontend/src/lib/{data-tables-api,data-tables-types}.ts`

**Interfaces:** System record editor submits `SystemRecordUpdate`; user table editor preserves `RecordCreate/RecordUpdate`. Records panel uses server-provided `business_url`, never constructs core URLs from guessed table names.

- [ ] **Step 1: Write failing record interaction tests**

  Assert system records show a read-only “核心信息” section and editable “扩展信息”; core inputs cannot be changed or submitted; business-page link matches the response. Assert no-extension state gives admins “新增字段” guidance and writers a read-only explanation. Assert system tables use one free-text search box and hide dynamic-field filter/sort plus record archive/status controls; user tables retain all existing controls. Save errors retain draft, revision 0 first save uses one stable `request_id`, and history says it covers extension changes only.

- [ ] **Step 2: Run focused tests and verify RED**

  Run: `pnpm test -- src/components/settings/data-tables/record-editor.test.tsx src/components/settings/data-tables/records-panel.test.tsx src/components/settings/data-tables/history-panel.test.tsx`

- [ ] **Step 3: Implement the split editor and system save path**

  Render core fields from `source=core`, extension controls from `source=extension`, preserve drafts on refresh/error, and keep a stable client request UUID until the first save result is confirmed. For system tables, debounce and submit `q` without offering unsupported extension-field filtering/sorting. Keep user table forms, links, archive actions and filters unchanged.

- [ ] **Step 4: Verify GREEN, TypeScript and production build**

  Run focused tests, `pnpm exec tsc --noEmit`, then `pnpm build` because this task changes routed UI behavior.

- [ ] **Step 5: Commit**

  Commit message: `feat: edit core table extensions in unified records`.

## Task 7: End-to-End Verification, Debt Cleanup and Local Handoff

**Files:**
- Modify: `v3_backend/test_v2/e2e/test_custom_data_tables.py`
- Create: `v3-frontend/src/components/settings/data-tables/unified-data-tables-flow.test.tsx`
- Modify: `docs/custom-data-tables-2026-10-06/{PROGRESS,VERIFICATION,RELEASE_PLAN}.md`
- Modify: `/Users/yichuanzhang/Desktop/curise_system_2/PROGRESS.md`

**Interfaces:** Public API only; local acceptance uses synthetic products, suppliers, orders and user tables. No production calls or writes.

- [ ] **Step 1: Write a full public-path acceptance test**

  Create one extension field on each system table, save values on visible synthetic records, verify refresh and history, create a normal user table, verify its link behavior still works, and assert core business row snapshots are unchanged. Add one unauthorized order fixture proving list, detail, save and history all hide it.

- [ ] **Step 2: Run the unified module verification**

  Run backend dynamic-data unit/integration/e2e tests including real PostgreSQL; run all data-table frontend tests and TypeScript. Fix only evidence-backed failures and rerun the affected scope.

- [ ] **Step 3: Review and clean scoped technical debt**

  Inspect the entire branch for duplicated table-kind branching, obsolete “custom data table” UI copy, unused helpers, unbounded adapter queries and bypasses of original permissions. Consolidate dispatch in `service.py/system_tables.py`; do not refactor unrelated product, order or PO code.

- [ ] **Step 4: Run one stable-candidate full verification**

  Run once: backend full pytest, architecture check, touched-file Ruff, frontend full Vitest, TypeScript and production build. Record exact pass/skip/warning counts; do not repeat the whole suite unless shared code changes afterward.

- [ ] **Step 5: Start local acceptance services**

  Upgrade only the isolated PostgreSQL 0035→0036, seed synthetic core records, start the test backend and production frontend build, and verify the settings route returns 200. Provide the local URL and test account; do not submit production data.

- [ ] **Step 6: Update progress and commit**

  Record completed work, remaining user visual checks, unverified boundaries and “not merged/pushed/deployed”. Commit message: `test: verify unified data table workflow`.

## Execution Order and Stop Conditions

Execute Tasks 1→7 in order. A task does not start until its targeted tests pass and its commit is recorded. If adapter access rules cannot reproduce the existing order visibility contract, stop before exposing orders rather than widening access. If 0035→0036 loses or rewrites any existing dynamic data, stop before continuing to UI work.

Production release is not part of this plan. After local user acceptance, merging, GitHub CI, backup, 0034→0035→0036 migration, Cloud Run/Oracle Job/Vercel deployment and production verification require a separate explicit authorization and updated release checklist.
