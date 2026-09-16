# 焦距 2 — 集成测试 (Integration Tests)

> **新定位**（2026-05-13 修订）：守 **HTTP API 契约**和 **DB 状态**。
> 这一层不验证"做对了"，只验证"接对了线"。

最后核对：2026-05-13

---

## 这一层的目的

> **防止端点接错线 / RBAC 漏掉 / DB 写不进。**
> 一个 HTTP 请求从路由进入，应该返回正确状态码 + 正确 schema + DB 应有的写入应该发生。

**这一层不该承担的事**：
- 验证 agent 推理对不对（→ 焦距 3 evals）
- 验证生成的 Excel 内容对不对（→ 焦距 3 workflow E2E）
- 验证用户多步工作流走得通（→ 焦距 3 workflow E2E）

这一层用 FastAPI TestClient + in-memory SQLite + **scripted LLM**。

---

## 现状（**131 个测试，16 个文件**）

| 路由 | 端点数 | 已测 | 漏测 | 测试文件 |
|---|---:|---:|---:|---|
| `auth.py` | 5 | 5 | 0 | `test_auth_api.py` (10) |
| `users.py` | 5 | 5 | 0 | `test_users_api.py` (9) |
| `orders.py` | 15 | 8 | 7 | `test_orders_api.py` (13) |
| `inquiry.py` | 8 | 5 | 3 | `test_inquiry_api.py` (8) |
| `documents.py` | 12 | 8 | 4 | `test_documents_api.py` (14) |
| `chat.py` | 8 | 3 | 5 | `test_chat_acceptance.py` (7) |
| `settings.py` | ~25 | 16 | ~9 | `test_settings_api.py` (16) |
| `masterdata.py` | ~24 | 11 | ~13 | `test_masterdata_api.py` (11) |
| `data_upload.py` | 1 | 1 | 0 | `test_data_upload_api.py` + `test_data_upload_e2e.py` (2 测试，建议合并) |
| `files.py` | 1 | 1 | 0 | `test_files_api.py` (7) |
| `excel.py` | 2 | 0 | 2 | — |
| `line_bind.py` | 1 | 1 | 0 | `tests/integration/line/test_bind_endpoint.py` (8) |
| `webhook.py` | 1 | 1 | 0 | `tests/integration/line/test_webhook_*.py` (20) |
| `phase6_stubs.py` | ~10 | 3 | — | 测试在 settings_api.py 里（**保留**，廉价保险） |

---

## 缺口

### 🔴 必补 —— 4 个端点 0 集成覆盖，影响核心业务

| ID | 端点 | 影响 |
|---|---|---|
| **G1** | `POST /api/orders/{id}/financial-analysis` | 汇率换算调用 API 后什么都不验，挂了无感 |
| **G2** | `POST /api/chat/actions/{id}/decide` | HITL approve → 执行的契约 |
| **G3** | `POST /api/orders/{id}/reprocess` | 重新提取触发的 contract |
| **G4** | `GET /api/orders/{id}/file-preview` | 签名 URL 生成的 contract |

### 🟡 值得补 —— 8 个，但优先级低

| ID | 端点 | 备注 |
|---|---|---|
| G5 | `POST /api/excel/parse` + `/parse-cells` | 模板分析依赖此 |
| G6 | `POST /api/documents/{id}/create-order` | 文档→订单转化 |
| G7 | `PATCH /api/documents/{id}/metadata` | 字段覆盖 |
| G8 | `POST /api/documents/{id}/user-tags` add/delete | 标签管理 |
| G9 | `GET /api/orders/{id}/files` + `/files/{filename}` | 附件列表/下载 |
| G10 | `DELETE /api/chat/sessions/{id}` | session 软删除 |
| G11 | `GET /api/orders/{id}/inquiry-data-preview/{sid}` | 询价数据预览 |
| G12 | masterdata 的 PATCH / DELETE 端点（~13 个） | 主数据维护 |

### 🟢 故意不在这一层测的（**重要修正**）

| 项 | 为什么不在这一层 | 在哪一层 |
|---|---|---|
| **生成的 inquiry Excel 内容对不对** | 这是 "做对了" 不是 "接对了"，且需要 openpyxl 读真文件 | 焦距 3 workflow |
| **SSE 流（`/chat/stream`, `/inquiry-stream`）的事件序列** | SSE 流是行为，不是契约 | 焦距 3 workflow |
| **`POST /generate-inquiry` 触发的后台生成是否正确** | 后台任务的真正运行 | 焦距 3 workflow |
| **`/exchange-rates/fetch` 拉真汇率 API** | 集成层不该打外部网络 | 焦距 3 workflow（可选） |
| **`/order-templates/analyze-pdf` 真 Gemini 分析** | 同上 | 焦距 3 workflow |

---

## 该补什么（焦距 2 维度）

**只补 4 个文件，全部 P0**：

| 优先级 | 新增文件 | 内容 | 堵的洞 |
|---|---|---|---|
| P0 | `tests/integration/test_financial_analysis_api.py` | 3-5 测试：原币、JPY→USD、汇率缺失降级 | G1 |
| P0 | `tests/integration/test_propose_action_decide_api.py` | approve → 执行 + reject → 不执行 + 跨用户拒绝 | G2 |
| P0 | `tests/integration/test_orders_lifecycle_api.py` | reprocess + file-preview + files 列表 + files 下载 | G3, G4, G9 |
| P0 | `tests/integration/test_documents_followup_api.py` | create-order + metadata PATCH + user-tags CRUD | G6, G7, G8 |

**清理一次**：
- 合并 `test_data_upload_api.py` + `test_data_upload_e2e.py` → `test_data_upload_pipeline.py`
- **不删** Phase 4/6 stub 测试（廉价保险，防止意外实现）

---

## 维护原则（新）

1. **新加 API 端点 → 同步加集成测试**，没测试别 merge
2. 集成测试**不应该**调真外部服务（Gemini / LINE / Serper / 真 SMTP）—— 用 monkeypatch 或 Fake
3. 集成测试**不验证 "生成的文件内容对"** —— 那是焦距 3 的事
4. 一个集成测试应该**< 1 秒**，超过就说明它在做工作流验证，应该拆出去
