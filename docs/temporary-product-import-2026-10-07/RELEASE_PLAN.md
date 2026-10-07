# 临时产品导入隔离发布与回退方案

## 发布硬边界

- 只允许目标 Cloud SQL 实例 `cruise-v3-db-clean-20261006`、数据库 `cruise_v3_clean`。
- 后端必须是新的 Cloud Run 服务名；不得更新正式 `cruise-v3-backend-*` 服务，不得接入 Scheduler 或 Oracle Job。
- 前端必须是独立、未提升正式域名的 Vercel 项目／部署；不得修改 `cruise-v3-frontend.vercel.app` 的 alias。
- 后端同时要求 `TEMP_DATABASE_SETUP_ENABLED=true` 和 `TEMP_DATABASE_SETUP_EXPECTED_DATABASE=cruise_v3_clean`；数据库名不符必须拒绝启动。
- 只使用合成主数据和合成产品做冒烟，禁止复制生产业务数据。

## 顺序

1. 固定已通过 CI 的提交 SHA；记录正式数据库 head/核心计数、正式 Cloud Run revision、Oracle Job generation 和正式 Vercel deployment。
2. 记录新实例 RUNNABLE、删除保护及新库 0036／业务表为空，创建按需备份。
3. 通过 Cloud SQL Auth Proxy 只连接新实例，执行 0036→0037、业务分类幂等 seed、fresh-database audit 和唯一索引直接验证。
4. 从固定 SHA 构建一次后端镜像，部署独立 Cloud Run；只授予新库 Secret，设置临时开关和精确 CORS 来源。
5. 部署独立前端，设置临时后端 URL 和 `NEXT_PUBLIC_TEMP_DATABASE_SETUP_ENABLED=true`，不提升正式 alias。
6. 验证健康、登录、状态、配置字段链接、模板下载、上传检查、差异核对、提交、产品详情和回滚；最终有效业务计数回到冒烟前。
7. 再读正式环境指纹，确认数据库、服务、Job 和正式域名未变化。

## 停止与回退

- 任一部署前指纹漂移、备份失败、数据库名不符、迁移 head 不唯一、空库审计失败或 CI 未通过：立即停止，不迁移／不部署后续步骤。
- 应用回退：关闭或删除独立临时前端；将独立 Cloud Run 最小实例设为 0 或删除服务。正式应用无需切流。
- 数据回退：冒烟批次优先使用应用回滚；若 0037 结构本身需撤回，在不保留任何有效业务数据的前提下连接新库执行 0037→0036，随后再次审计。不得对生产库执行该命令。
- 完全撤销新实例需单独确认：先保留备份，再显式关闭删除保护并删除 `cruise-v3-db-clean-20261006`；这不属于默认发布步骤。
