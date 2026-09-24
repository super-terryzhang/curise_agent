# PO 商品问题展示与人工处理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把上一轮唯一待处理行统计与 v3 商品问题展示、人工修正、单位换算和询价 warning 标注作为一个可部署版本交付。

**Architecture:** 后端生成统一 `issue_overview` 读模型并提供原子化单行处理接口，前端只负责展示后端的状态与处理目标。匹配器持久化人工关联，异常规则为询价输出提供 warning，Excel 渲染器仅标注允许进入询价的提醒行。

**Tech Stack:** FastAPI、SQLAlchemy、Pydantic、pytest、Next.js 16、React 19、TypeScript、Vitest、openpyxl、Cloud Run、Vercel。

**Spec:** `docs/superpowers/specs/2026-09-24-order-issue-resolution-design.md`

## Global Constraints

- 不新增数据库迁移，不修改原始 PO 文件，不删除历史询价版本。
- 新业务匹配状态保持二元：只有 `matched` 与未确认匹配；历史 `possible_match` 按未匹配兼容展示。
- 系统不得猜测单位换算或拆箱规则；只有人工证据可以解除单位阻断。
- 匹配状态和询价处理结果必须独立，warning 不得解除 blocking/error。
- 未知 finding 必须展示原始原因、建议和证据，并保留安全兜底。

## Review Focus

- 同名商品重复出现且没有 line id：finding 不得错误归到另一行；测试使用重复名称和不同 row_index。
- finding 来自同一供船安排中的另一张 PO：必须按 `source_order_id` 归属；沿用并扩展跨 PO 统计测试。
- 人工关联商品不在当前港口/有效期候选池：接口应拒绝或保持未匹配，不得伪造成功。
- 行级保存后重匹配或异常检测失败：请求必须返回错误且不能把无效人工结果宣称为已处理。
- warning 与排除问题同时存在：整体询价结果必须为排除，Excel 不得包含该行。

---

### Task 1: 统一问题读模型与动态处理目标

**Files:**
- Create: `v3_backend/domains/orders/issues.py`
- Modify: `v3_backend/domains/orders/schemas.py`
- Modify: `v3_backend/domains/orders/service.py`
- Test: `v3_backend/test_v2/section_4_orders/test_order_issue_overview.py`
- Test: `v3_backend/test_v2/section_9_web_api/test_orders_api.py`

**Interfaces:**
- Consumes: `anomaly.run_anomaly_check(order, pipeline=...)` 与同安排订单保存的跨 PO findings。
- Produces: `build_issue_overview(order, related_orders) -> dict`；`OrderDetail.issue_overview`。

- [ ] **Step 1: 写失败测试**：覆盖一行多问题、跨 PO finding、重复名称、历史 `possible_match`、未知 code 兜底和匹配/询价状态分离。
- [ ] **Step 2: 运行 `pytest test_v2/section_4_orders/test_order_issue_overview.py test_v2/section_9_web_api/test_orders_api.py -q`，确认因 `issue_overview` 不存在而失败。**
- [ ] **Step 3: 实现 `issues.py`**：用稳定行标识归并 findings，生成 `rows`、`non_row_findings`、`actionable_row_count`、`warning_row_count` 和有限的 resolution target；未知 code 返回 `review`。
- [ ] **Step 4: 在 `_to_detail` 中构建读模型**：复用当前 related-orders 权限查询，把上一轮 `actionable_count` 设置为 overview 的唯一错误行数量。
- [ ] **Step 5: 重跑专项测试并提交**：`git commit -m "feat: expose actionable order issue overview"`。

### Task 2: 人工商品关联与单行原子处理

**Files:**
- Modify: `v3_backend/domains/orders/matching/code_first.py`
- Modify: `v3_backend/domains/orders/schemas.py`
- Modify: `v3_backend/domains/orders/service.py`
- Modify: `v3_backend/apps/http/orders.py`
- Test: `v3_backend/test_v2/section_4_orders/test_product_matching.py`
- Test: `v3_backend/test_v2/section_9_web_api/test_orders_api.py`

**Interfaces:**
- Consumes: `OrderRowResolveRequest`，动作 `edit_source | bind_product | record_conversion`。
- Produces: `PATCH /api/orders/{order_id}/products/{row_index}/resolve -> OrderDetail`；匹配结果 `match_reason="人工关联商品"`。

- [ ] **Step 1: 写失败测试**：人工商品 ID 优先于代码、候选池外商品不匹配、编辑源行清除旧人工证据、绑定商品、登记换算、非法输入和跨用户 404。
- [ ] **Step 2: 运行两份专项测试，确认缺少人工匹配和 resolve 路由而失败。**
- [ ] **Step 3: 修改 code-first matcher**：只接受候选池中的 `manual_product_id`，命中时分数为 1.0 且保留来源字段，失败时不进入模糊匹配。
- [ ] **Step 4: 实现请求 schema、service 与 HTTP 路由**：三种动作分别校验并写回目标 JSON 行，重新匹配和运行异常检测后一次提交；异常时回滚。
- [ ] **Step 5: 重跑专项与相邻安排匹配测试并提交**：`git commit -m "feat: resolve individual PO product issues"`。

### Task 3: 前端问题视图模型与 API 类型

**Files:**
- Create: `v3-frontend/src/lib/order-issue-view.ts`
- Create: `v3-frontend/src/lib/order-issue-view.test.ts`
- Modify: `v3-frontend/src/lib/orders-api.ts`

**Interfaces:**
- Consumes: 后端 `issue_overview` 与 resolution target。
- Produces: `filterIssueRows`、`issueStatusText`、`resolutionLabel`、`resolveOrderProductRow`。

- [ ] **Step 1: 写失败 Vitest**：覆盖四种筛选、多个问题摘要、unknown fallback、warning+error 取最严格询价结果及历史状态。
- [ ] **Step 2: 运行 `pnpm vitest run src/lib/order-issue-view.test.ts`，确认模块不存在而失败。**
- [ ] **Step 3: 增加严格 TypeScript 类型与纯函数**：不得在组件内重新编码后端阻断规则，仅负责文案、筛选和安全 fallback。
- [ ] **Step 4: 增加 resolve API 客户端并重跑 Vitest/TypeScript。**
- [ ] **Step 5: 提交**：`git commit -m "feat: add order issue presentation model"`。

### Task 4: 实现 v3 商品表、问题详情与修正弹窗

**Files:**
- Create: `v3-frontend/src/components/orders/order-product-issues.tsx`
- Create: `v3-frontend/src/components/orders/order-row-resolution-dialog.tsx`
- Modify: `v3-frontend/src/app/dashboard/orders/[id]/page.tsx`
- Modify: `v3-frontend/src/app/dashboard/data/page.tsx`
- Modify: `v3-frontend/src/app/dashboard/data/ProductsTab.tsx`

**Interfaces:**
- Consumes: Task 3 的纯函数/API；现有 `listProducts`、订单 PATCH、原始文件预览和数据管理页。
- Produces: v3 四筛选商品表、右侧多问题详情、五字段修正、商品搜索关联、单位换算证据表单与主数据深链。

- [ ] **Step 1: 先扩充 Task 3 的失败测试，固定组件所需状态转换、保存 payload 与深链 URL。**
- [ ] **Step 2: 运行目标 Vitest，确认新增期望失败。**
- [ ] **Step 3: 实现两个聚焦组件并替换页面内旧单 finding 逻辑**：同一行显示全部问题；按钮使用后端 resolution target；保存成功刷新 OrderDetail。
- [ ] **Step 4: 为数据管理页增加 `product`/`action` 查询参数**：可定位产品并打开编辑或价格期间；无效 ID 回退为搜索结果而不崩溃。
- [ ] **Step 5: 运行 Vitest、`tsc --noEmit` 与生产 build，提交**：`git commit -m "feat: add PO issue resolution workspace"`。

### Task 5: 询价 warning 预览与 Excel 黄色标注

**Files:**
- Modify: `v3_backend/domains/orders/anomaly.py`
- Modify: `v3_backend/domains/inquiry/orchestrator.py`
- Modify: `v3_backend/domains/inquiry/template_engine.py`
- Modify: `v3_backend/apps/http/inquiry.py`
- Test: `v3_backend/test_v2/section_5_inquiry/test_template_engine.py`
- Test: `v3_backend/test_v2/section_5_inquiry/test_inquiry_endpoints.py`
- Test: `v3_backend/test_v2/section_e2e/test_po_automation_stages.py`

**Interfaces:**
- Consumes: 匹配结果上的当前 warning findings。
- Produces: `inquiry_warnings` 行快照、预览 `warnings` 与 Excel 浅黄色 `FFF2CC` 行标注。

- [ ] **Step 1: 写失败测试**：warning 行在通用/模板工作簿中为黄色、预览返回文案、同时有排除问题的行不进入供应商文件。
- [ ] **Step 2: 运行三份专项测试，确认现有空 warnings 和无填色行为失败。**
- [ ] **Step 3: 抽取单行 anomaly 判定并由 orchestrator 写入 warning snapshot**：不复制价格规则，不改变排除顺序。
- [ ] **Step 4: 模板引擎对实际输出列应用 `FFF2CC`，预览聚合 warning 文案。**
- [ ] **Step 5: 重跑专项并提交**：`git commit -m "feat: highlight inquiry warning rows"`。

### Task 6: 收敛、文档与完整验证

**Files:**
- Modify: `docs/order-issue-resolution-2026-09-24/PROGRESS.md`
- Modify: root `PROGRESS.md` after merge/deploy
- Create: root `DEPLOYMENT_VERIFIED_2026-09-24_ORDER_ISSUES.md` after production verification

**Interfaces:**
- Consumes: Tasks 1–5 与基线提交 `06caab7`。
- Produces: 可审查提交、完整测试证据、发布与回退记录。

- [ ] **Step 1: 清理重复旧 helper、过期单 finding UI 和无效文案，保持未知问题 fallback。**
- [ ] **Step 2: 运行 Ruff、架构门禁、后端完整 pytest、前端完整 Vitest、TypeScript 与生产 build。**
- [ ] **Step 3: 按 spec 逐项自查并进行整分支代码 review；Important/Critical 问题以新的失败测试修复。**
- [ ] **Step 4: 更新仓库内进度文档并提交**：`git commit -m "docs: record order issue resolution verification"`。

### Task 7: 合并、推送与生产发布

**Files:**
- Modify after verification: root `PROGRESS.md`
- Create after verification: root `DEPLOYMENT_VERIFIED_2026-09-24_ORDER_ISSUES.md`

**Interfaces:**
- Consumes: 通过完整验证的分支与 GitHub CI。
- Produces: GitHub main、Cloud Run revision、同步镜像的 Oracle Job、Vercel production deployment 和只读生产核验证据。

- [ ] **Step 1: 推送功能分支，创建并合并 PR；确认 main CI 前后端均成功。**
- [ ] **Step 2: 确认 Alembic head 仍为 0030，本次无迁移；从 main 精确归档构建后端镜像。**
- [ ] **Step 3: 以 0% 流量部署 Cloud Run 候选并核验 health、OpenAPI、新 resolve 路由未认证 401、CORS 与 ERROR 日志，再切换 100% 流量。**
- [ ] **Step 4: 将 Oracle Job 更新为同一镜像 digest；不暂停或改动三个 Scheduler 的启用状态。**
- [ ] **Step 5: 部署 Vercel 候选，核验登录/订单详情/数据管理路径后提升正式域名。**
- [ ] **Step 6: 只读核对数据库 head 和核心数量、检查新 revision 日志，更新根进度与部署核验文档并推送最终文档提交。**

