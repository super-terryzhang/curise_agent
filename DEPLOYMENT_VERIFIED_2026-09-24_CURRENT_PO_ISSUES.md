# 当前 PO 待处理统计修复生产核验（2026-09-24）

核验时间：2026-09-24 23:16–2026-09-25 00:09 JST。

## 结论

订单列表现在只统计当前 PO 的匹配和数据问题，不再把历史询价版本中的 `RFQ_ROW_EXCLUDED` 排除结果当成当前未匹配。无需生成新询价版本，PO165047CCI 已按当前数据库结果计算为 56 个商品、需处理 3 项；PO167144CCI 为 1 个商品、需处理 1 项。

本轮没有数据库迁移、没有前端代码发布，也没有生成或覆盖询价版本。数据库保持 `0031_unit_conversion_rules`，现有询价历史和审计数据原样保留。

## 根因与修复边界

`/dashboard/orders` 使用供船安排摘要 API。该 API 为避免加载所有订单的完整匹配 JSON，会从持久化的 `anomaly_data.findings` 计算唯一待处理商品行；规则激活后的异常重算同时写入了旧询价 version 1 的 56 条 `RFQ_ROW_EXCLUDED`，导致这些下游历史结果覆盖了当前 53 行已匹配、3 行未匹配的事实。

修复将 `RFQ_ROW_EXCLUDED` 明确定义为不可变询价版本的历史结果：

- 它继续保留在询价历史和原始异常快照中用于审计；
- 不再计入订单列表和供船安排的当前待处理商品数；
- 不再作为 PO 问题总览的当前错误；
- `PRODUCT_NOT_MATCHED`、单位、数量、供应商和价格等真实当前问题仍正常统计。

## 源码与验证

- 功能提交：`fa90bcacb2f9ca7aab67e4cc60995794e48523df`。
- GitHub PR：[#8](https://github.com/super-terryzhang/curise_agent/pull/8)，合并提交 `00b4d46be672fad3cb5b6be788e741b9e0e7b2f8`。
- GitHub CI：run `36013352290`，frontend 52 秒、backend 8 分 8 秒，均成功。
- 本地定向回归：17 passed；完整后端：1826 passed、79 skipped、4 个既有弃用 warning。
- Ruff：三个修改的生产文件和聚焦测试通过；完整历史测试文件仍有既有分号/导入格式债务，本轮未扩大清理。
- 构建来源：合并提交的精确 Git 归档，SHA-256 `7a305366c6d911499a2896efbf96e6301400ac070f5d8a7ae25f50bb92d70d23`，不包含工作区文档。
- Cloud Build：`1d80d1a5-09b3-42d3-a43e-18951371a98d`。
- 镜像 digest：`sha256:b16675fb1b43cca5781c883d7226ca9d4f57e73c320514e987c99a359bfe9d51`。

## 生产发布与数据核验

后端候选 `cruise-v3-backend-po-current-issues-20260924` 先以 0% 流量部署。候选 `/health` 返回 200 和正确 revision，未登录安排 API 返回 401，运行 digest 与构建结果一致，候选 ERROR 日志为空。

候选镜像通过只读生产数据库 execution `cruise-v3-unit-conv-preflight-20260924-8rktz` 的硬性断言：

| PO | 商品数 | 当前需处理 |
|---|---:|---:|
| PO165047CCI | 56 | 3 |
| PO167144CCI | 1 | 1 |

核验确认数据库 head 为 `0031_unit_conversion_rules`。随后正式后端切换为该 revision 接收 100% 流量；正式后端 `/health` 返回新 revision，正式 `/dashboard/orders` 返回 HTTP 200。

Oracle Job 已同步相同镜像，generation 为 17；`oracle-po-hourly`、`bulk-image-gc-hourly`、`fx-refresh-daily` 均保持 ENABLED。临时核验 Job 已重新锁定，不能误重复执行生产脚本。

2026-09-25 00:00 JST 的首次新版自动执行 `cruise-v3-po-hourly-qbj2l` 由 Scheduler 正常创建，明确使用 generation 17 和上述 digest，并在 1 分 35 秒后成功结束（`succeededCount=1`）。业务 scan run 366 为 `error_code=null`，32 项由 28 个历史待采纳、3 个需复核和 1 个延期组成；它与上一轮 run 365 的项目 JSON 哈希完全相同（`041f7a2174de6d45c4dcff6d94d309215efde18490863d00b6ddac9e5eb7c8f0`），没有新导入结果或新询价。

只读复核 execution `cruise-v3-unit-conv-preflight-20260924-lc9j7` 与哈希复核 `cruise-v3-unit-conv-preflight-20260924-49p6b` 均成功。此前 `8q47g` 因核验命令使用错误模块路径，在导入数据库模块前即退出，未查询或写入数据；执行参数是一次性覆盖，Job 模板随后核验仍为 `READ_ONLY_RELEASE_CHECK is required` 锁定命令。

## 回退

应用回退只需把 Cloud Run 流量切回 `cruise-v3-backend-uc-on-20260924`；Oracle Job 镜像可同步切回 `sha256:fafc1b01f6b360d136ae3bb3c512a00a3164465168ba8fdad02fd198d75f6100`。本轮没有迁移或数据写入，不需要数据库回退；历史询价版本也未被修改。
