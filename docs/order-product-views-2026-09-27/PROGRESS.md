# 订单与产品双视图发布进度（2026-09-27）

## 已实现

- 订单管理保留原有“按 PO”列表，并新增“按轮次”视图；轮次汇总直接展示装船日期、船名、目标港口、PO 数、商品数和当前处理状态，未分配订单保留独立入口。
- 轮次视图会显示待处理商品数及待人工确认的 AI 港口决定，不会把尚未确认的港口判断隐藏为正常。
- 产品管理保留原有列表，并新增以主图为中心的图库视图；没有图片的产品明确显示上传入口，卡片保留价格、状态和更多操作。
- 图库支持“最新录入、产品名称 A–Z、产品名称 Z–A”服务端排序，切换展示方式、筛选或排序时会重置对应分页。
- 产品查询接口新增受控 `sort` 参数，非法排序返回 422；现有调用不传参数时继续使用原默认顺序。

## 验证证据

- 前端最终全量：27 个测试文件、162 项测试通过；TypeScript 检查与 Next.js 生产构建通过。
- 后端最终全量：`1859 passed, 101 skipped`；架构门禁 0 违规，改动范围 Ruff、wheel 构建/安装与资源导入均通过。
- 功能 PR #12 合并为 `cbf3ea2f4a53f06a2f4a227ba3e9408d44a08346`；PR CI `36323525411` 与 main CI `36324040924` 的前后端任务均成功。
- 生产只读页面验收确认：前端 build 标识为 `cbf3ea2`；订单共 78 张 PO、37 个轮次（含 1 个未分配）；产品共 1463 个，列表与图库均可正常切换和分页。

## 生产发布

- 后端 `cruise-v3-backend-order-product-views-20260927` 接收 100% 流量，镜像 digest 为 `sha256:9f65a9f0f8e282fe97af94c1377769ba2827a05c5e65f76cba29fb47b4b49fb4`；Cloud Build `eca9a198-0aaf-40a9-8ae1-ecbb48464074` 成功。
- 前端 `dpl_GKYbbR6pUPLanhstsgauAmTJmWZu` 为 Production / Ready，正式域名页面已由用户验收通过。
- Oracle Job generation 20 使用与后端相同的不可变镜像；三个 Scheduler 均保持 ENABLED。
- 本轮没有数据库迁移或业务数据写入，数据库保持 `0032_llm_port_resolution`；发布期间未手动触发 Oracle 扫描。
- 新 revision 的 ERROR 日志只有既有 passlib/bcrypt `__about__` 兼容告警，与本功能无关。
- 完整发布、回退和生产核验证据见工作区根目录 `DEPLOYMENT_VERIFIED_2026-09-27_ORDER_PRODUCT_VIEWS.md`。

## 回退边界

- 后端可把流量切回 `cruise-v3-backend-llm-port-20260927`，Oracle Job 同步恢复上一不可变镜像。
- 前端可重新提升 `dpl_FozJ2JshqUEE583KPSunGhKUtPyR`。
- 本轮没有数据库结构或数据变化，因此不需要数据库 downgrade。
