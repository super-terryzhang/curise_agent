# Existing Product Update Design

## Objective

Add a dedicated “已有产品更新” module to the workbench so operational users can select existing products, choose what they intend to update, download current data, upload the edited workbook, inspect deterministic validation and changes, and explicitly confirm the write.

## Confirmed user flow

1. 工作台入口：保留现有模块，新增“已有产品更新”。
2. 选择产品：复用产品主数据的搜索、类别、供应商、国家、港口和状态筛选。
3. 更新范围：只能选择基本信息、采购价区间、卖价区间；至少选择一项。
4. 下载与上传：按所选产品和范围生成 Excel，在同一页重新上传修改后的 `.xlsx`。
5. 程序检查：继续使用现有服务端表头、身份、金额、日期、区间归属、重叠和版本校验；失败时不写入。
6. 核对与提交：检查通过后显示真实新旧值，用户确认后才调用既有提交接口。
7. 完成：显示批次结果，并复用既有上传记录与安全回滚入口。

The visible progress bar is five stages: “选择产品 / 更新范围 / 下载与上传 / 程序检查 / 核对与提交”. A failed validation remains in stage 4; a successful validation moves directly to stage 5.

## Architecture

This is a frontend orchestration feature. It reuses the existing product list APIs, current workbook exporter, and `/api/data-upload/workbench/*` upload/validate/rows/commit/history/rollback contract. No new database table, migration, product write path, or alternative validation engine is introduced.

The exporter gains an explicit update scope. It always preserves the identity columns required by the existing parser, includes only selected price-period rows, blanks/hides out-of-scope editable columns, and hides technical ID/revision columns from ordinary Excel view without removing them. The server remains authoritative after upload and again at commit time.

## UX and boundaries

- Follow the current production shell and existing shadcn components; do not introduce the old blue mockup system.
- Employees, admins and superadmins can use the module; finance cannot access product update tools.
- Selection persists across visible filter/page changes, but “select all” applies only to the currently loaded page.
- The new module updates existing products only. If validation classifies a row as a new product, it is a blocking issue in this dedicated flow and must be handled through “产品数据上传”.
- Validation also blocks products outside the current selection and changed fields outside the selected scope, so an old or unrelated workbook cannot silently expand the update boundary.
- Product images remain in the separate image-upload module.
- No AI matching, approval workflow, automatic pricing, notification, or new master-data field is added.
- A previously exported file can still be uploaded through the existing generic product-data upload module. The dedicated flow keeps selection/scope in the current browser session and verifies both against validated rows; the workbook remains independently parseable.

## Failure handling

- Product/filter load failure: keep the page usable and expose retry.
- Export failure: do not download a partial workbook.
- Wrong file type, upload failure or storage failure: show the existing localized error and keep product data unchanged.
- Validation issue: show Excel row, product, server reason and conservative remediation text; do not enable commit.
- Product/version conflict at commit: show the server conflict and return to review; never overwrite newer data.
- Reset before commit: cancel an already-resolved uncommitted batch through the existing cancel endpoint.

## Acceptance

- The workbench exposes the module only to existing product-writer roles.
- Selected products and scope produce a workbook containing all and only intended price-period rows while preserving parser-required identity.
- System IDs and revisions are present but hidden in the workbook.
- A valid exported workbook round-trips through existing server validation and commit contracts.
- Invalid rows are visible and commit stays disabled.
- Review shows real operation/field/before/after data returned by the server.
- Completion and upload history use existing result and rollback behavior.
- Frontend focused tests, complete frontend tests, TypeScript, production build, related backend workbench contract tests, and a local user-path smoke check pass.

## Visual reference

`../../../../UI_UX_design/07_existing_product_update/` contains the approved eight-screen reference set. The codebase’s current layout and design tokens are authoritative where raster text or spacing differs.
