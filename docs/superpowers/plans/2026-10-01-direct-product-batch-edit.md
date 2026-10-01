# 页面内已有产品批量编辑 Implementation Plan

> **For agentic workers:** 本轮由主代理在当前会话逐步实施；使用测试优先和阶段验收，沿用风险分层验证。用户已确认设计并明确授权开始执行，不重复申请计划确认。

**Goal:** 让用户在工作台直接筛选地区产品、修改基本信息或价格区间、批量新增期间，并检查核对后保存。
**Architecture:** 新增专用 JSON 暂存入口，将受控页面操作转成既有 upload staging，复用版本、金额、日期、期间归属、差异和审计/回滚。workflow_version=3 区分直接编辑；该模式提交全部成功才保存，既有 Excel 模式保持原契约。产品 ID 定位，编辑主资料独立于定位信息；无新表、无迁移。
**Tech Stack:** Next.js / React / TypeScript / Vitest；FastAPI / Pydantic / SQLAlchemy / pytest / openpyxl。
**Spec:** 工作区 UI_UX_design/08_existing_product_batch_edit/README.md 与六张最终稿。

## Global Constraints

- 采购价与卖价完全独立；不重新引入产品整体有效期。
- 精确选择已有区间；没有对应区间的产品明确列出，不猜测目标，不自动新增。
- 跨页全选取得当前筛选全部数据，任何分页失败不能造成部分全选。
- 统一修改只改勾选字段，仍允许逐行调整；新增价格必须人工填写。
- 基本信息仅使用已有字段；同一产品版本在多期间行中保留。
- 服务端校验不由前端替代；人工确认保存前不写业务产品/期间。
- finance 不获得产品维护入口；沿用 ProductUploader 的既有角色。
- Excel 上传继续存在；清理失去用途的旧页面子组件，不移除共享上传能力。
- 小阶段聚焦测试，最终稳定候选一次前端完整测试/构建和相关后端完整回归；不对每阶段重复整套后端。
- 本轮开发交付明确尚未上线；合并和发布进度另记。

## Review Focus

- 多页筛选变化/请求失败：全选不能漏页或混入新筛选。
- 产品多期间：保留各自金额、期间 ID，采购卖价不串行。
- 日期重叠/缺价：具体行可定位，错误不能进入保存。
- 核对后并发变更：不覆盖新版本，直接模式整体拒绝。
- 基本信息名称/代码/国家/港口改变：始终 ID 定位，校验引用和唯一性；旧 Excel 身份契约保持。

## Task 1: 服务端专用暂存和完整保存边界

**Files:** create v3_backend/domains/masterdata/upload/direct_updates.py; modify upload/__init__.py, upload/service.py, apps/http/data_upload.py; create test_v2/section_6_masterdata/test_direct_product_updates.py 和 section_9_web_api/test_direct_product_updates_api.py。
**Purpose:** 不让直接编辑绕过服务端保护；保存行为与预览一致。
**Interfaces:** prepare_direct_update(db, request, user_id) → WorkflowBatch；DirectUpdateRequest: selected_product_ids, scope basic|purchase|selling, operation edit|add, rows[product_id, expected_revision, period_id?, values]。沿用 rows、commit、cancel、rollback HTTP。
- [x] 写行为测试：修改两产品日期保留各自价格；新增采购/卖价多个期间；基本身份字段编辑；未知字段/越界所选产品/错误期间/finance拒绝；预览后冲突全批不写；回滚恢复。
- [x] 运行测试并确认缺少入口失败。
- [x] 实现受控请求校验、稳定定位暂存、直接字段差异、直接模式保存原子性。
- [x] 聚焦领域/HTTP测试、Ruff与架构检查通过，保存证据。

## Task 2: 选择、目标区间和编辑数据模型

**Files:** create v3-frontend/src/lib/product-batch-edit.ts, product-batch-edit.test.ts, product-batch-edit-api.ts；modify product-selection-step.tsx。
**Purpose:** 实现跨页全选、原日期精确定位、统一值与逐行值分离。
**Interfaces:** matchPeriodTargets(products,type,from,to)；createEditRows(products,scope,operation,targets)；applyUniformValues(rows,patch)；buildDirectUpdateRequest(products,scope,operation,rows)；prepareDirectProductUpdate(request)。
- [x] 写独立测试：已停用期间排除、缺目标列出、金额0保留、只有结束日期变化、添加第二期间、不伪造价格、未变行不提交。
- [x] 运行失败测试，然后实现纯函数、API调用和选品全选控制。
- [x] 聚焦测试与类型检查通过。

## Task 3: 页面内三种编辑分支

**Files:** create operation-step.tsx, edit-data-step.tsx；modify product-update/page.tsx, workbench-modules.ts；modify page/setup UI tests。
**Purpose:** 四步流程实现选品 → 操作 → 编辑 → 核对保存。
- [x] 对页面控件/可编辑行及统一字段行为补充验证。
- [x] 实现全选快照、操作精确目标、基本信息下拉、日期应用、多期间添加/移除、价格缺失提示。
- [x] 验收无下载上传强制步骤；单价不同的产品统一改日期后仍保留各自单价。

## Task 4: 核对问题、确认保存和技术债清理

**Files:** create direct-review-step.tsx；reuse completion-step.tsx；remove unused workbook-step.tsx/update-scope-step.tsx/validation-step.tsx/review-step.tsx 及仅旧流程使用的状态机/工作簿文件和测试（删除前 rg核对引用）；保留共享导出/Excel上传工具。
**Purpose:** 问题可定位返回编辑、旧新值高亮、无重复工作流。
- [x] 服务端行号映射回页面编辑行，检查含错误时同时展示通过和错误行。
- [x] 保存前取消可退出；核对后编辑使旧批次失效；保存失败保留输入；完成结果不把失败冒充成功。
- [x] 回归旧产品上传和现有导出，确认无无用引用、无产物/凭据入库。

## Task 5: Review、连接验证和交付

**Files:** plan、PROGRESS.md、本轮验证证据。
**Purpose:** 证明跨前后端流程和现有功能仍可运行，提供用户可核查结果。
- [x] 本地合成数据走 prepare → 全量核对 → commit → 历史/rollback；无生产写入。
- [x] 前端完整 Vitest、TypeScript、生产构建；后端相关上传/主数据回归，稳定候选一次完整后端验证。
- [x] 最终独立代码复核，修复重要发现并聚焦验证。
- [x] 更新进度、明确实施/测试结果、未部署与剩余人工验收。

## Ledger

- 起点：main@73c6c60，独立工作树 .worktrees/batch-product-edit-20261001；干净主工作区。
- 2026-10-01：已阅读最新生产核验、工程经验、价格执行记录和批准设计。
- 设计决定：基本字段以 ID 绑定的独立 changes 保存，不能通过修改 Excel 身份来实现；直接编辑的版本3批次整体保存，通用 Excel 上传保持逐行容错。

- 交付：最终代码已完成；39项最终相关后端、244项前端、3项真实临时PostgreSQL并发验证通过，生产构建与架构检查通过。完整核验证据见同目录 `2026-10-01-direct-product-batch-edit-verification.md`。
- Ruling: 直接模式回滚按全批原子恢复 — 同批身份交换与中途版本冲突不能造成部分恢复/重复身份 — 代价是一个冲突使整批暂不自动回滚，保留人工核对路径；Excel回滚仍保留既有逐产品保护。
- Ruling: 完整回归运行一次，最终复核新增修复使用相关39项与真实PostgreSQL3项验证 — 遵循工程经验的风险扩测，避免每个小修改重复8分钟整套 — 完整回归与后续聚焦验证时间点分开记录，不声称全套在每个补丁后都重跑。
- 清理：工作树依赖已改成本地副本以兼容Turbopack；本机合成服务和临时数据库验收后停止；不修改生产配置来绕过构建。
