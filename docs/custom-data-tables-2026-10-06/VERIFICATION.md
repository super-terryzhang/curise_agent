# 统一数据表：本地交付与验收

日期：2026-10-06。分支 `feature/custom-data-tables-20261006`，工作树 `curise_agent/.worktrees/custom-data-tables-20261006`。
**尚未合并、推送、运行 GitHub CI、迁移生产或部署。生产仍是 0034；本文仅证明本地候选。**

## 交付范围

- 设置中心只有一个“数据表管理”入口，目录同时显示产品、供应商、订单和用户创建的数据表。
- 产品、供应商和订单核心字段继续来自原业务表并显示为锁定；不允许删除、改类型或通过扩展接口修改核心值。
- 管理员可以在系统表增加扩展字段，授权角色可以录入扩展值；扩展数据使用独立锚点、版本、审计历史和幂等 request ID。
- 系统表支持分页和核心信息关键词搜索，并提供原产品/供应商/订单业务页面链接；用户表继续支持八种字段、关联、筛选、排序、归档和恢复。
- 订单沿用既有权限：管理员可见全部，普通员工只见自己的订单；列表、详情、写入和历史使用同一范围。
- 第一轮仍不支持系统表扩展字段关联、修改系统核心字段、公式、附件和历史回滚。

## 自动化证据

| 检查 | 结果与边界 |
|---|---|
| 统一模块聚焦验证 | 130 passed / 9 warnings；含公开 API、真实 PostgreSQL、0035→0036、权限、并发与 SQL 分页 |
| 后端稳定候选全量 | 2050 passed / 89 skipped / 11 warnings，419.43 秒；本轮只运行一次 |
| 前端全套 | 54 文件 / 287 passed；包含统一目录、锁定核心字段、扩展编辑和组合流程 |
| TypeScript | `pnpm exec tsc --noEmit` exit 0 |
| 前端生产构建 | `NEXT_PUBLIC_API_URL=http://localhost:8003 pnpm build` exit 0；统一列表和动态详情路由均产出 |
| Ruff / 架构 / diff | 触及范围 Ruff 通过，`scripts/check_arch.py` 0 violations，`git diff --check` 通过 |

89 个跳过项均有明确条件：真实 LLM 慢评测、未提供的商业 PDF 样本，以及认证和批量编辑各自固定端口的专用 PostgreSQL 并发库。新统一数据表的专用 PostgreSQL 地址已提供并实际运行；跳过项没有被描述为通过。11 个告警是既有 Starlette/passlib、Alembic 配置、上传状态常量和重复 uploads Operation ID 告警。

## 公共路径与数据保护证明

后端完整验收通过真实登录和 `/api/data-tables` 公开接口，为产品、供应商和订单各创建一个扩展字段、保存、刷新并读取历史。保存前后逐列比较原 `products`、`suppliers`、`v2_orders` 行完全一致；扩展值仅存在 `v3_data_records` 等动态表中。

另一个员工账号只能列出自己的订单；对他人订单的详情和保存均为 404，按他人来源 ID 查询历史返回 0。系统历史查询已增加回归测试，确认权限条件、总数和 `LIMIT/OFFSET` 在 SQL 层执行，不会随历史总量把全部记录载入内存。

## 本地人工验收环境

- 页面：`http://localhost:3005/dashboard/settings/data-tables`
- 后端：`http://localhost:8003`
- 数据库：仅本机 PostgreSQL 17，`127.0.0.1:55447/cruise_data_tables_test`，schema `custom_demo_20261006`，head `0036_unified_data_tables`
- 管理员：`custom-admin@example.test` / `CustomTables!2026`
- 员工：`custom-writer@example.test` / `CustomTables!2026`
- 目录：产品、供应商、订单、验收客户、验收业务记录
- 新合成核心样本：2 个产品、1 个供应商、2 张订单；员工 HTTP 冒烟只返回 `LOCAL-WRITER-PO`

页面和后端健康地址均返回 200；正式生产构建提供页面，而非开发服务器。所有样本均有 `LOCAL` 或“本地验收”标识，不来自生产，也没有写入生产。

## 用户检查建议

1. 用管理员登录，确认目录中系统表与用户表没有视觉割裂。
2. 打开“产品”，确认核心字段显示为只读；新增一个扩展字段后，为 `LOCAL-APPLE` 填值并检查修改历史。
3. 打开“订单”，管理员应看见两张本地订单；员工重新登录后只应看见 `LOCAL-WRITER-PO`。
4. 打开原有“验收业务记录”，确认八类型、关联、分页和历史仍正常。

## 未执行边界

- 未合并 main、推送 GitHub、运行 GitHub CI、备份生产、迁移生产、部署 Cloud Run/Oracle Job/Vercel 或写入生产数据。
- 未用真实 LLM、真实 Oracle、真实产品/订单执行写入验收。
- 本地视觉与操作体验仍由用户最终判断；自动化通过不代替用户验收。
