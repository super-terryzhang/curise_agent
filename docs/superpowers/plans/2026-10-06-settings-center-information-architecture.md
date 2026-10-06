# 设置中心信息架构优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将“数据表管理”提升为统一一级入口，并把设置中心改造成两组六项的分类首页，同时保持现有设置功能与旧链接可用。

**Architecture:** 前端新增统一的数据表路由定义与正式页面路由，旧设置子路由只负责无损跳转；导航使用路由边界匹配，避免 `/dashboard/data` 与 `/dashboard/data-tables` 同时命中。设置中心用查询参数驱动“分类首页／单项详情”，原六个设置组件原样复用。

**Tech Stack:** Next.js 16、React 19、TypeScript、Tailwind CSS、Vitest、Testing Library。

**Spec:** `docs/superpowers/specs/2026-10-06-settings-center-information-architecture-design.md`

## Global Constraints

- 页面统一使用“数据表管理／数据表”，不再显示“自定义数据表”。
- 数据表管理正式地址为 `/dashboard/data-tables`；旧 `/dashboard/settings/data-tables` 必须兼容跳转并保留查询参数。
- 设置中心仍仅供 `superadmin`、`admin`；数据表管理保持 `superadmin`、`admin`、`employee`、`finance` 可见。
- 不修改六个设置组件的 API、字段、保存规则或后端权限。
- 不增加设置搜索、收藏、最近使用、统计、推荐或新的业务字段。
- 本轮只做前端信息架构及文档，不修改后端和数据库迁移。

## Review Focus

- `/dashboard/data-tables` 不能因 `/dashboard/data` 前缀相同而双重高亮；Task 1 的边界匹配测试必须覆盖。
- 员工或财务直接打开旧数据表地址时不能先被路由守卫赶回工作台；Task 1 与 Task 2 的旧路径测试必须覆盖。
- 旧详情链接的 `tableId`、`tab`、`record` 必须完整保留；Task 2 的重定向测试必须覆盖。
- 未知 `section`、重复参数或空参数不能产生空白设置页；Task 3 的参数测试必须覆盖。
- 设置详情保存失败仍由原组件显示且输入不因外层导航重置；Task 3 必须证明详情切换只挂载被选择的原组件，不包裹或改写其提交逻辑。

---

## File Structure

- `v3-frontend/src/lib/dashboard-routes.ts`：集中定义数据表正式／旧路径、路径边界判断和旧路径规范化。
- `v3-frontend/src/lib/dashboard-routes.test.ts`：纯函数覆盖相似前缀、旧详情路径和无关路径。
- `v3-frontend/src/app/dashboard/layout.tsx`：一级导航、角色可见性、精确激活和路由守卫。
- `v3-frontend/src/app/dashboard/layout.test.tsx`：四种角色入口、导航行为与旧地址访问保护。
- `v3-frontend/src/app/dashboard/data-tables/page.tsx` 与 `[tableId]/page.tsx`：新的正式页面路由。
- `v3-frontend/src/components/settings/data-tables/legacy-data-tables-redirect.tsx`：保留旧路径后缀和查询参数的单一跳转组件。
- `v3-frontend/src/components/settings/data-tables/legacy-data-tables-redirect.test.tsx`：列表与详情跳转验证。
- `v3-frontend/src/app/dashboard/settings/data-tables/page.tsx` 与 `[tableId]/page.tsx`：仅调用兼容跳转组件。
- `v3-frontend/src/components/settings/data-tables/*.tsx`：把内部链接切换到正式路径。
- `v3-frontend/src/app/dashboard/settings/settings-center.tsx`：分类首页、六项目录和详情容器。
- `v3-frontend/src/app/dashboard/settings/settings-center.test.tsx`：目录、详情、返回及非法参数测试。
- `v3-frontend/src/app/dashboard/settings/page.tsx`：为查询参数驱动的客户端组件提供页面入口。
- `docs/custom-data-tables-2026-10-06/PROGRESS.md`：记录本轮变更、验证证据和未部署状态。

### Task 1: 统一导航路径与权限判断

**Files:**
- Create: `v3-frontend/src/lib/dashboard-routes.ts`
- Create: `v3-frontend/src/lib/dashboard-routes.test.ts`
- Modify: `v3-frontend/src/app/dashboard/layout.tsx`
- Modify: `v3-frontend/src/app/dashboard/layout.test.tsx`

**Interfaces:**
- Produces: `DATA_TABLES_PATH`, `LEGACY_DATA_TABLES_PATH`, `isPathWithin(pathname: string, basePath: string): boolean`、`canonicalizeDashboardPath(pathname: string): string`。
- Produces: 一级导航中唯一的“数据表管理”项，四种角色可见；“设置中心”只对管理员角色可见。

- [ ] **Step 1: 写路由纯函数失败测试**

在 `dashboard-routes.test.ts` 断言：精确路径和真实子路径命中；`/dashboard/data-tables` 不命中 `/dashboard/data`；旧列表和详情路径规范化到新路径；`/dashboard/settings-other` 不命中设置中心。

- [ ] **Step 2: 运行纯函数测试并确认失败**

Run: `pnpm exec vitest run src/lib/dashboard-routes.test.ts`

Expected: FAIL，因为路由模块尚不存在。

- [ ] **Step 3: 实现最小路由函数**

在 `dashboard-routes.ts` 定义两个固定路径常量；`isPathWithin` 只接受完全相等或 `${basePath}/` 前缀；`canonicalizeDashboardPath` 只替换旧数据表根路径及真实子路径。

- [ ] **Step 4: 扩展布局失败测试**

修改 `layout.test.tsx`，断言 `employee`、`finance`、`admin`、`superadmin` 均看到“数据表管理”；员工和财务看不到“设置中心”；点击入口进入 `/dashboard/data-tables`；旧路径对员工仍为允许状态；数据管理与数据表管理不会同时处于 `aria-current` 状态。

- [ ] **Step 5: 运行布局测试并确认旧行为失败**

Run: `pnpm exec vitest run src/app/dashboard/layout.test.tsx`

Expected: FAIL，页面仍显示“自定义数据表”或管理员没有独立入口。

- [ ] **Step 6: 修改导航和守卫**

在 `layout.tsx` 使用 `DATA_TABLES_PATH` 新增单一导航项，删除角色专用的“自定义数据表”；活动判断与访问判断统一使用 `canonicalizeDashboardPath` 和 `isPathWithin`，按钮为当前项时设置 `aria-current="page"`。

- [ ] **Step 7: 运行 Task 1 测试**

Run: `pnpm exec vitest run src/lib/dashboard-routes.test.ts src/app/dashboard/layout.test.tsx`

Expected: PASS。

- [ ] **Step 8: 提交 Task 1**

```bash
git add v3-frontend/src/lib/dashboard-routes.ts v3-frontend/src/lib/dashboard-routes.test.ts v3-frontend/src/app/dashboard/layout.tsx v3-frontend/src/app/dashboard/layout.test.tsx
git commit -m "refactor: separate data tables from settings navigation"
```

### Task 2: 建立正式数据表路由并兼容旧链接

**Files:**
- Create: `v3-frontend/src/app/dashboard/data-tables/page.tsx`
- Create: `v3-frontend/src/app/dashboard/data-tables/[tableId]/page.tsx`
- Create: `v3-frontend/src/components/settings/data-tables/legacy-data-tables-redirect.tsx`
- Create: `v3-frontend/src/components/settings/data-tables/legacy-data-tables-redirect.test.tsx`
- Modify: `v3-frontend/src/app/dashboard/settings/data-tables/page.tsx`
- Modify: `v3-frontend/src/app/dashboard/settings/data-tables/[tableId]/page.tsx`
- Modify: `v3-frontend/src/components/settings/data-tables/table-list.tsx`
- Modify: `v3-frontend/src/components/settings/data-tables/table-detail.tsx`
- Modify: `v3-frontend/src/components/settings/data-tables/record-editor.tsx`
- Modify: `v3-frontend/src/components/settings/data-tables/linked-record-picker.tsx`
- Modify: `v3-frontend/src/components/settings/data-tables/records-panel.tsx`
- Modify: `v3-frontend/src/components/settings/data-tables/shared.tsx`
- Modify: existing affected `*.test.tsx` expectations under `v3-frontend/src/components/settings/data-tables/`

**Interfaces:**
- Consumes: Task 1 的 `DATA_TABLES_PATH` 与 `LEGACY_DATA_TABLES_PATH`。
- Produces: `LegacyDataTablesRedirect({ tableId?: string }): JSX.Element`，使用当前查询参数调用 `router.replace()`。
- Produces: `/dashboard/data-tables` 和 `/dashboard/data-tables/[tableId]` 正式页面。

- [ ] **Step 1: 写旧链接跳转失败测试**

测试列表地址跳到 `/dashboard/data-tables?status=archived&page=2`，详情地址跳到 `/dashboard/data-tables/table-1?tab=records&record=row-2`；只调用一次 `replace`，加载期间显示明确状态文本。

- [ ] **Step 2: 运行跳转测试并确认失败**

Run: `pnpm exec vitest run src/components/settings/data-tables/legacy-data-tables-redirect.test.tsx`

Expected: FAIL，因为跳转组件尚不存在。

- [ ] **Step 3: 实现正式页面与旧路由跳转**

正式页面复用现有 `TableList`、`TableDetail` 和 `parseTableTab`；旧页面只渲染 `LegacyDataTablesRedirect`，组件从 `useSearchParams()` 生成查询字符串并用 `router.replace()` 进入正式地址。

- [ ] **Step 4: 更新所有数据表内部链接**

把列表、详情标签、返回入口、关联记录、错误定位与字段提示中的 `/dashboard/settings/data-tables` 全部替换为 `DATA_TABLES_PATH`；同步修改现有测试的期望 URL。

- [ ] **Step 5: 验证没有遗留面向用户的旧路径或旧名称**

Run: `rg -n '自定义数据表|/dashboard/settings/data-tables' v3-frontend/src --glob '!**/*.test.*'`

Expected: 仅允许旧路由文件、旧路径常量或兼容跳转说明命中；业务链接不得命中。

- [ ] **Step 6: 运行数据表前端测试**

Run: `pnpm exec vitest run src/components/settings/data-tables src/lib/data-tables-view.test.ts src/lib/data-tables-api.test.ts`

Expected: PASS。

- [ ] **Step 7: 提交 Task 2**

```bash
git add v3-frontend/src/app/dashboard/data-tables v3-frontend/src/app/dashboard/settings/data-tables v3-frontend/src/components/settings/data-tables v3-frontend/src/lib/dashboard-routes.ts
git commit -m "feat: add canonical data tables routes"
```

### Task 3: 把设置中心改造成方案 A 分类首页

**Files:**
- Create: `v3-frontend/src/app/dashboard/settings/settings-center.tsx`
- Create: `v3-frontend/src/app/dashboard/settings/settings-center.test.tsx`
- Modify: `v3-frontend/src/app/dashboard/settings/page.tsx`

**Interfaces:**
- Produces: `SettingsCenter({ section }: { section?: string }): JSX.Element`。
- Produces: 固定 section ID：`fields | orders | suppliers | delivery | company | ai`。
- Consumes: 现有六个设置组件，不改变其 props 或行为。

- [ ] **Step 1: 写分类首页失败测试**

断言无 `section` 时显示“订单与询价”“公司与系统”及六项入口；页面不显示“数据表管理”按钮或横向 tabs；每项链接使用对应 `?section=`。

- [ ] **Step 2: 写详情与边界失败测试**

分别断言合法 section 只挂载对应原组件并显示“返回设置中心”；非法、空白及重复参数解析后的非白名单值显示首页；返回链接是 `/dashboard/settings`。

- [ ] **Step 3: 运行设置中心测试并确认失败**

Run: `pnpm exec vitest run src/app/dashboard/settings/settings-center.test.tsx`

Expected: FAIL，因为分类首页组件尚不存在。

- [ ] **Step 4: 实现设置目录和详情容器**

`settings-center.tsx` 用固定配置数组定义两组六项的 ID、标题、说明和图标；首页渲染统一高度列表行，详情按白名单映射渲染一个现有组件，不通过动态字符串导入或猜测 section。

- [ ] **Step 5: 接入页面查询参数**

`page.tsx` 从 Next 页面 `searchParams` 读取 `section`；只把单个字符串传给 `SettingsCenter`，数组或缺失值按 `undefined` 处理，以便未知输入稳定回到首页。

- [ ] **Step 6: 运行 Task 3 测试**

Run: `pnpm exec vitest run src/app/dashboard/settings/settings-center.test.tsx src/app/dashboard/layout.test.tsx`

Expected: PASS。

- [ ] **Step 7: 提交 Task 3**

```bash
git add v3-frontend/src/app/dashboard/settings/page.tsx v3-frontend/src/app/dashboard/settings/settings-center.tsx v3-frontend/src/app/dashboard/settings/settings-center.test.tsx
git commit -m "feat: reorganize settings into categorized home"
```

### Task 4: 回归验证、技术债务检查和进度记录

**Files:**
- Modify: `docs/custom-data-tables-2026-10-06/PROGRESS.md`

**Interfaces:**
- Consumes: Tasks 1–3 的完整前端行为。
- Produces: 可复核的本地验证证据与明确的未合并、未部署状态。

- [ ] **Step 1: 运行本轮聚焦测试**

Run: `pnpm exec vitest run src/lib/dashboard-routes.test.ts src/app/dashboard/layout.test.tsx src/app/dashboard/settings/settings-center.test.tsx src/components/settings/data-tables`

Expected: PASS，无未处理异常。

- [ ] **Step 2: 运行前端完整测试一次**

Run: `pnpm test`

Expected: 全部测试 PASS；既有告警单独记录，不将其表述为本轮新增失败。

- [ ] **Step 3: 运行生产构建**

Run: `pnpm build`

Expected: Next.js production build 成功，正式路由与旧兼容路由均出现在构建结果中。

- [ ] **Step 4: 审查改动和技术债务**

Run: `git diff --check && rg -n '自定义数据表|/dashboard/settings/data-tables' v3-frontend/src --glob '!**/*.test.*'`

Expected: 无空白错误；旧措辞不存在；旧路径只保留在兼容实现和常量中；没有复制六个设置组件或数据表页面主体。

- [ ] **Step 5: 更新进度文档**

在 `PROGRESS.md` 记录方案 A、改动文件、测试与构建结果、已知债务，以及“尚未合并 main、尚未推送、尚未部署生产”。

- [ ] **Step 6: 提交文档**

```bash
git add docs/custom-data-tables-2026-10-06/PROGRESS.md docs/superpowers/specs/2026-10-06-settings-center-information-architecture-design.md docs/superpowers/plans/2026-10-06-settings-center-information-architecture.md
git commit -m "docs: record settings information architecture work"
```

- [ ] **Step 7: 交付本地验收**

报告提交、聚焦测试、完整测试和构建证据，并给出本地设置中心与数据表管理地址；不宣称已上线。

## Self-Review Result

- Spec coverage：一级导航、方案 A 首页、六项详情、旧链接、角色权限、文案、视觉边界、错误处理和非目标均由 Tasks 1–4 覆盖。
- Step scan：每一步只有一个可观察动作或检查结果；实现细节只固定公开接口和业务值。
- Type consistency：四个共享接口均在首次产生任务中定义，后续任务只消费固定名称。
- Review focus：五项高风险输入分别落入 Tasks 1–3 的明确测试。
- Proportion：计划只描述路径、接口、测试和验收，不复制页面实现代码。
