# PO 进入系统时间进度

## 已完成

- 订单管理“按 PO”表格新增“进入系统时间”，位置在 PO 编号后。
- 后端供船安排列表显式返回订单既有 `created_at`；前端按日本时区显示为 `MM/DD HH:mm`。
- 后端接口测试、前端组件测试、前端全量测试、生产构建和后端完整回归已通过。
- 功能 PR #16 已合并为 `66342a41272237556fdc138bf2ba19072bc5fc13`。
- PR CI `36328794967` 与合并后的 main CI `36329276165` 前后端均成功。
- 后端 `cruise-v3-backend-order-entry-time-20260928` 已接收 100% 流量，镜像 digest 为 `sha256:7069fca8eac15bc58718a4e054e3caa413e29b2c3b5adf767dff70297c540e86`。
- 前端 `dpl_6cJPPGSuPgBN1Q4cHFcwXvLz2gv1` 已成为 Production / Ready 并绑定正式域名。
- Oracle Job generation 21 已同步同一不可变镜像；数据库未迁移，未触发扫描或修改业务数据。

## 仍需用户验收

- 使用正式账号打开订单管理并选择“按 PO”，确认新增列的位置、时间可读性和业务含义符合实际使用习惯。

## 回退边界

- 后端回退到 `cruise-v3-backend-order-product-views-20260927`。
- 前端重新提升 `dpl_GKYbbR6pUPLanhstsgauAmTJmWZu`。
- Oracle Job 恢复镜像 `sha256:9f65a9f0f8e282fe97af94c1377769ba2827a05c5e65f76cba29fb47b4b49fb4`。
- 没有数据库迁移，无需数据库回退。
