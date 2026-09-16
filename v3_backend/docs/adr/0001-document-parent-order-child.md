# ADR-0001: Document 是父类型，Order 是其中一种子类型

**日期**: 2026-04-24
**状态**: Accepted
**关联 PLAN**: ADR-1

## 背景

v2 的模型设计把"一个文件"和"一个采购订单"混在了一起。`Order` 表同时承载了：

1. 文件本身（`file_url`, `file_type`, `file_size_bytes`）
2. 文件中提取的结构化字段（`po_number`, `ship_name`, `products`）
3. 采购订单特有的业务状态（`match_results`, `inquiry_data`, `fulfillment_status`）

第 1 类对**任何**文档都成立——发票、报价、装箱单也都是文件。第 2、3 类是采购订单专有的。

这导致：
- v2 后期引入 `Document` 表时，PO 专有字段被"偷偷"塞进 `Document.extracted_data` JSON blob 里（`metadata`, `products`, `field_evidence` 子键）
- 想加新的文档类型（Invoice、Quote）时，要么复制整套 `Document` 逻辑，要么再往 JSON blob 里塞——两条路都会让代码越来越乱

## 决策

采用 **"父类型 + 类型化子投影"** 的建模（类似 Documentum 的 SysObject 层级 + Kintone 的 app/record 结构）。

### 分层结构

```
Document (父，所有文档类型共享)
├── 通用字段: id, user_id, filename, file_url, file_type, file_size_bytes
├── 分类: doc_type (判别列)
├── 通用抽取产出: extracted_blocks (Document AI blocks + bbox + stats)
├── 状态: status, extraction_method, content_markdown, extracted_at
└── 无业务字段

Order (purchase_order 子类型)
├── FK: document_id → Document (1:1)
├── PO 结构化字段: po_number, ship_name, vendor_name, delivery_date,
│                   order_date, currency, destination_port, total_amount, products
├── PO workflow 状态: match_results, match_statistics, inquiry_data,
│                     fulfillment_status, anomaly_data, financial_data
└── 关联: country_id, port_id, user_id

Invoice (未来子类型)
├── FK: document_id → Document (1:1)
├── 发票字段: invoice_number, issue_date, due_date, tax_amount, line_items
├── 发票 workflow: payment_status
└── ...
```

### 两步处理 + 注册表

1. **分类（Classify）**: `domains.document.classifier.classify(blocks) → doc_type`
   - 规则式（关键词、结构），可插拔
   - 每个子类型通过 `classifier_rules.py` 注册自己的识别规则
2. **投影（Project）**: `domains.document.projector_registry[doc_type].project(doc, db) → <子类型>`
   - 每个子类型注册自己的投影器
   - Document 不知道有哪些子类型——它通过注册表查

### 依赖方向

- **Document 不 import 任何子类型**（单向依赖）
- 子类型知道自己来自 Document（`Order.document_id` FK）
- 子类型在 `__init__.py` 或显式注册时调用 `register_projector(doc_type, fn)`

## 放弃的方案

### 方案 A：单表继承（single table inheritance）

所有类型共享一张 `documents` 表，不同类型有不同可空列。
- ❌ 表会膨胀到几十上百列
- ❌ 每种类型的字段约束不同（Invoice 的 tax_amount 应该非空，Order 不需要）难以表达
- ❌ 查询"所有 Invoice"时过滤性能差

### 方案 B：EAV（entity-attribute-value）

通用的 `documents` + 通用的 `fields` 表（key-value）存所有字段。
- ❌ 查询性能极差
- ❌ 类型安全完全丢失
- ❌ 现有代码要重写

### 方案 C：纯微服务拆分

每种文档类型一个独立服务。
- ❌ 10 人团队 + 5 年项目早期不需要分布式复杂度
- ❌ 跨文档类型的查询（"本月所有文档"）要做跨服务聚合

## 影响

- **Phase 2** 建 Document 表时**严格只放通用字段**
- **Phase 3** 建 Order 表时，把 v2 `Document.extracted_data.metadata/products` 里的字段**物理迁移**到 Order 新列（expand / backfill / contract 三段式）
- **Phase 8** 加 Invoice 时验证：`git diff` 不碰 `domains/document/`、`domains/orders/`

## 验证

- [ ] Phase 3 完成后，查询 `SELECT po_number FROM orders WHERE id = X` 直接返回值（不读 Document JSON）
- [ ] Phase 8 加 Invoice 的 diff 不触及现有 domain
- [ ] `domains/document/` 全文 `grep "Order\|Invoice"` 返回 0 匹配
