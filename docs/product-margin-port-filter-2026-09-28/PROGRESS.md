# 产品利润率与港口筛选进度

## 已完成

- 产品读取接口派生返回 `profit_margin`，不新增数据库列；公式为 `(contract_price - price) / contract_price × 100%`，并按系统金额与利润率两位小数口径使用 `ROUND_HALF_UP`。
- 产品列表、图库、新增表单和编辑表单均显示利润率；缺失价格或卖价不大于零时显示未配置，负利润率保留并使用警示色。
- 产品页新增 ID 驱动的港口筛选，覆盖列表、图库、分页和导出，并与其他筛选组合生效。
- 本地完整回归、review 后针对性复核、PR CI 与合并后的 main CI 均通过。
- 功能 PR #18 已合并为 `5ddfd3e8217aea8e7252e3fef4b476fb6b12b43d`。
- 后端 `cruise-v3-backend-product-margin-20260928` 已接收 100% 流量，镜像 digest 为 `sha256:7f379ea66d79f8822bfe4c16a6a137f27c47a94a6bfddb2d0830abb783be8afe`。
- 前端 `dpl_Dh6qvEPbmnhSQME8ifP91EZfCuvt` 已成为 Production / Ready 并绑定正式域名。
- Oracle Job generation 22 已同步同一不可变镜像；数据库未迁移，任务未手动执行，业务数据未写入。
- 正式页面已完成只读验收：1463 个产品，大阪筛选 169 个；列表、图库与编辑预览中的利润率显示一致。

## 当前状态

本项开发、合并、部署和生产核验均已完成，没有遗留的功能开发步骤。既有 passlib/bcrypt `__about__` 兼容告警继续作为独立技术债处理。

## 回退边界

- 后端回退到 `cruise-v3-backend-order-entry-time-20260928`。
- 前端重新提升 `dpl_6cJPPGSuPgBN1Q4cHFcwXvLz2gv1`。
- Oracle Job 恢复镜像 `sha256:7069fca8eac15bc58718a4e054e3caa413e29b2c3b5adf767dff70297c540e86`。
- 没有数据库迁移，无需数据库回退。
