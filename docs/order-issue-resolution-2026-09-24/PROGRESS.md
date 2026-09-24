# PO 商品问题处理优化进度

日期：2026-09-24

## 本轮范围

- 修复订单列表/详情把 finding 数量误当作待处理商品行数量的问题；同一行多个问题只计一个待处理商品。
- 增加统一 `issue_overview`，完整呈现每行匹配状态、询价处理结果、全部问题、原因、建议、证据与处理入口。
- 增加单行人工处理：修正 PO 商品字段、关联当前业务范围内的数据库商品、登记有人工依据的单位换算。
- 询价 warning 固化到询价版本与数据预览，并在 Excel 实际商品行使用浅黄色 `FFF2CC` 标注；阻断行仍不进入文件。

## 已完成

1. 后端问题读模型及跨 PO finding 归属，兼容历史 `possible_match`、重复商品名、缺失行号与未知规则。
2. 原子化行处理 API：`PATCH /api/orders/{order_id}/products/{row_index}/resolve`，写入后重新匹配和异常检测。
3. 匹配器支持 `manual_product_id`，只接受当前国家、港口和产品有效期候选池内商品；更换商品会清除旧换算证据。
4. PO 详情页替换旧单 finding 界面，提供“全部 / 未匹配 / 已匹配有问题 / 可询价”筛选和完整问题详情。
5. 数据管理页支持 `product`、`search`、`action` 查询参数，可从问题详情定位产品编辑或价格期间。
6. 询价 warning 预览、通用 Excel 与供应商模板 Excel 黄色行标注；重新生成单一供应商时继续排除原有阻断行。
7. 自查发现并修复：未匹配 finding 重复、换商品沿用旧换算证据、未匹配商品错误登记换算、预览/单供应商重生成重新带入排除行。

## 当前验证状态

- 后端完整回归：`1764 passed, 94 skipped, 4 warnings`，耗时 485.16 秒；skip 均为需真实 LLM、PostgreSQL、受控 PDF 或外部凭据的既有外围测试。
- 前端完整回归：`16` 个测试文件、`107 passed`；`pnpm exec tsc --noEmit` 通过；Next.js 生产构建通过并生成 16 个路由。
- 架构门禁 `scripts/check_arch.py`、改动文件 Ruff、`git diff --check` 均通过。
- Python wheel 构建、独立安装与生产资源导入检查通过；wheel SHA-256 为 `05f19c435cb7c100b40b551b03a6d349caed3a615df5e1662c5a9554471484ef`。
- ESLint 10 因仓库没有 `eslint.config.*` 无法启动，这是既有工具链债务；不以其结果代替 TypeScript 或生产构建。

## 合并与生产部署

- GitHub PR `#3` 已合并；生产源码为 `main@c8b6c0141b413477d34c08c792be64422752c64b`，CI run `35956752342` 前后端均成功。
- Cloud Build `ffed93c8-7925-4646-9013-7001681878a5` 从精确 Git 归档成功构建 digest `sha256:9225ce40087de4e2422ac6d0d8285ad9d0e9a625478fb917a8cd6b74b5f4c357`。
- 0% 候选通过健康、OpenAPI 新路由、未登录 401、正式 Origin CORS 与错误日志检查；`cruise-v3-backend-order-issues-20260924` 现接收 100% 流量。
- 前端 `dpl_HSNN75qZpRSgoZJ4ZEkSY7kFdyAP` 已提升正式域名，登录、订单列表、PO 详情与供船安排路由均返回应用页面 200。
- 数据库无迁移并保持 `0030_direct_bulk_images`；最终只读核验为 1449 产品、73 订单、32 询价、621 张图片。
- Oracle Job generation 14 使用相同 digest，环境摘要更新前后相同；14:00 JST 首次自动执行成功，业务 run 356 未创建订单或询价。三个 Scheduler 均保持 `ENABLED`。

## 尚待用户验收

- 用真实账号打开一个未匹配商品，检查原因说明并完成一次修正字段或关联商品。
- 检查一个已匹配 warning 行是否仍能生成询价，并确认预览与下载 Excel 的黄色行位置正确。
- 自动化没有向生产 PO 行写入测试修正；这部分主观交互与业务结果不能由只读发布检查替代。

## 发布边界

本轮没有数据库迁移，不修改原始 PO 文件，不删除历史询价版本。功能已部署生产；完整平台证据、已知债务和回退方式见根目录 `DEPLOYMENT_VERIFIED_2026-09-24_ORDER_ISSUES.md`。
