# 2026-10-01 已有产品更新五阶段流程生产核验

核验时间：2026-10-01 16:45–17:00 JST。结论：工作台“已有产品更新”五阶段流程已合并到 GitHub main 并发布到现有 Vercel Production；正式账号能够打开页面并读取真实产品列表。该功能仅修改前端编排，没有数据库迁移、后端发布、Oracle Job 更新或生产业务数据写入。

## 发布结果

| 项目 | 最终值 |
|---|---|
| 生产功能源码 | `f91b1286f827a6318f31fe2dd56f6e2e044f2587` |
| GitHub CI | `36832247722`，frontend / backend 均为 success |
| 前端 | `dpl_dtbFYWsW1pRU9t1C83JtYSsrrb3P`，Production / Ready |
| 正式域名 | `https://cruise-v3-frontend.vercel.app` |
| 后端 | 保持 `cruise-v3-backend-product-validity-20261001`，本轮未部署 |
| 数据库 | 保持 `0034_drop_product_validity`，本轮无迁移 |
| Oracle Job | 保持 generation 24，本轮未更新 |
| 前端回退点 | `dpl_Agv4fAKqLYKKjDsjPy5npSoSH1je` |

## 上线能力

- 工作台新增“已有产品更新”，仅向 `employee/admin/superadmin` 展示；`finance` 不获得产品更新入口。
- 用户按产品名/代码、类别、供应商、国家、港口和状态筛选已有产品，跨筛选和分页保留选择。
- 用户只选择基本信息、采购价区间和卖价区间三类真实范围；导出文件保留并隐藏产品 ID、revision 和价格期间 ID。
- 修改后的同一 `.xlsx` 在同一流程重新上传，继续使用既有服务端表头、身份、金额、日期、期间归属、重叠与版本检查。
- 新增产品、未选择产品和超出所选范围的字段会在专用流程中阻止提交；检查通过后展示字段级旧值/新值，用户二次确认才调用既有提交接口。
- 完成页复用既有上传历史和安全回滚入口；旧“产品数据上传”页的已有产品提示已改为进入该专用流程。

## 验证证据

- 本地稳定候选：前端 `44 files / 254 tests passed`，TypeScript 通过，Next.js 16.2.11 Turbopack 生产构建通过；相关后端工作台契约 `5 passed`。
- GitHub `main@f91b128` 的 CI run `36832247722`：frontend 成功，backend 完整行为套件、架构检查与 wheel 检查成功。
- Vercel deployment `dpl_dtbFYWsW1pRU9t1C83JtYSsrrb3P` 状态为 Production / Ready，并已绑定两个现有 Production alias。
- 正式 `/login` 返回 200；正式 `/dashboard/workbench/product-update` 返回 200；后端 `/health` 返回 200；来自正式前端域名的 API CORS 预检返回 200，并明确允许 `https://cruise-v3-frontend.vercel.app`。
- 使用已有正式 `System Administrator` 会话进行只读 UI 核验：页面显示五阶段、搜索以及类别/供应商/国家/港口/状态筛选，产品表格从生产后端成功加载，共 1549 个产品、78 页。
- 自动和人工核验均未选择产品、下载 Excel、上传文件、提交更新、创建批次或修改任何生产业务数据。

## 发布边界与回退

- 本轮仅发布前端；Cloud Run、数据库、Scheduler 和 Oracle Job 保持原生产版本，因此不需要备份、迁移或后台任务切换。
- 如新页面发生阻塞，可把上一 Vercel Production deployment `dpl_Agv4fAKqLYKKjDsjPy5npSoSH1je` 重新提升到正式域名；该回退不涉及数据库或后端。
- 用户下一步可在正式页面选择少量产品，先检查作用域文件和程序检查结果，在最终确认前取消；首次真实提交仍应选择可安全回退的测试产品。
