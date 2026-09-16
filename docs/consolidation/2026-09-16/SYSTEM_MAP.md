# 当前系统地图

本地图描述 2026-09-16 冻结基线的实际实现，不描述理想架构。

## 1. 外部入口

| 入口 | 接收层 | 主要业务路径 | 主要状态 |
|---|---|---|---|
| 人工文档/PO 上传 | `apps/http/documents.py`、`apps/http/orders.py` | document service → extraction workflow → order projection → automatic order pipeline | Document、Order、OrderGroup、Inquiry |
| Oracle 自动/手动扫描 | `apps/http/oracle_scan.py`、`apps/jobs/oracle_scan.py`、`apps/jobs/oracle_po.py` | Oracle adapter → 来源校验/保存 → 建单 → 匹配/归组/询价/异常检测 | Oracle scan/import、Document、Order、Inquiry |
| 产品 Excel 工作台 | `apps/http/data_upload.py` | parse → resolve/validate → preview → commit/rollback | UploadBatch、StagingProduct、Product、PricePeriod、PriceHistory |
| 数据管理 CRUD | `apps/http/masterdata.py` | masterdata service/repository | Product、Supplier、Port、Country、价格区间及图片 |
| 供船安排页面 | `apps/http/order_groups.py` | arrangement workspace → arrangement matching → versioned inquiry | OrderGroup、成员 Order、Inquiry version |
| 单 PO 页面 | `apps/http/orders.py`、`apps/http/inquiry.py` | 数据修改、重匹配、异常、询价兼容读取 | Order、match_results、anomaly_data、Inquiry |
| AI 工作台 | `apps/http/chat.py` | agent runtime → typed tools/approval → domain services | ChatSession、ChatMessage、PendingAction、业务对象 |
| LINE | `apps/line/webhook.py` | LINE session/agent/delivery → domain services | LineUser、LineEvent、Chat/业务对象 |

## 2. PO 到询价主路径

1. `domains/document/service.py` 保存原件和 Document；`workflow.py` 负责提取、分类和触发 projector。
2. `domains/orders/projection.py` 把采购单提取结果投影为 Order，保存 PO 元数据和商品行。
3. `domains/orders/automation.py` 是八阶段协调器：匹配、归组、安排级询价、异常检测，并把 trace 镜像到 Document 与 Order。
4. `domains/orders/matching/` 负责单 PO 匹配；`domains/orders/groups/matching.py` 负责整个供船安排的逐行统一匹配。
5. `domains/orders/groups/automation.py` 决定自动分组；`arrangements.py` 负责安排列表、工作区、人工编辑和重新分类。
6. `domains/inquiry/orchestrator.py` 同时承载旧单 PO询价和新版安排级版本询价；`_supplier_worker.py` 生成每个供应商文件。
7. `domains/inquiry/template_engine.py` 填充 Excel，`template_contract.py` 校验可编辑模板契约，`workbook_quality.py` 校验输出。
8. `domains/orders/anomaly.py` 汇总各阶段异常，结果保存在 `Order.anomaly_data`，供人工处理。

## 3. 产品上传主路径

1. 前端 `workbench/product-upload/page.tsx` 上传 `.xlsx`，上传后立即请求确定性校验。
2. `apps/http/data_upload.py` 只处理认证、文件边界和 HTTP 错误映射，主要逻辑进入 `domains/masterdata/upload/service.py`。
3. UploadBatch 保存原件哈希、表头诊断、状态和计数；StagingProduct 保存真实 Excel 行号、规范字段、匹配和逐行错误。
4. 预览从同一 staging 数据生成 create/update/skip/error；只有 `can_continue` 为真时允许 commit。
5. commit 写入 Product、兼容价格字段、多个价格区间、变更日志和价格账本；rollback 依赖批次日志和 revision 冲突保护。

## 4. 状态责任

| 状态 | 唯一主要责任方 | 备注 |
|---|---|---|
| 文档上传、提取、分类 | document domain | 原件存储由 storage abstraction 负责 |
| PO 商品、匹配、异常 | orders domain | `anomaly_data` 还承担八阶段 trace 的持久化 |
| 自动/人工归组 | orders/groups | 自动标记存入 `order_metadata[_automatic_grouping]` |
| 询价版本和供应商输出 | inquiry domain | 安排级每次生成新版本，旧版本保留 |
| 产品、价格期间、价格账本 | masterdata domain | 兼容字段和多期间表需要同步 |
| 上传批次和回滚证据 | masterdata/upload | 工作台和旧 Agent 上传目前复用同一大 service |
| 登录、会话、权限审计 | identity domain | HTTP dependency 负责把角色/能力映射到入口 |
| 异步任务执行 | `infrastructure/jobs/runner.py` | 入口层与 domain 通过同一基础设施抽象提交任务；规则 6 禁止 domain 反向依赖 apps |

## 5. 错误处理

- 领域错误通常由各 HTTP router 的 `_translate` 转成稳定状态码与消息。
- 自动 PO 流程捕获阶段异常，写入 pipeline trace、`processing_error` 和 anomaly，原则上进入人工队列。
- 询价供应商 worker 隔离单供应商失败，安排级状态可为 completed、partial、unmatched 或 error。
- 产品上传在 staging 阶段收集逐行问题；commit 前再次校验，并用 revision 防止覆盖并发修改。
- 后台 runner 当前记录协程任务异常，但无运行中跨进程持久化；Cloud Run 多实例不共享内存状态。

## 6. 前端边界

- `src/lib/*-api.ts` 定义浏览器侧 DTO 和 HTTP/SSE 调用。
- 页面负责流程状态与呈现；订单/文档/设置部分页面已经超过 1000 行，存在展示、请求和转换逻辑混合。
- 整单询价只在 arrangement 页面触发；PO 页面读取安排和供应商状态，并跳转到整单上下文。
