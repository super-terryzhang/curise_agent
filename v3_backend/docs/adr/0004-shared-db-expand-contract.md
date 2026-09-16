# ADR-0004: v3 与 v2 共享数据库，采用 expand/contract 模式演进 schema

**日期**: 2026-04-24
**状态**: Accepted
**关联 PLAN**: ADR-4

## 背景

v2 正在生产运行，存着真实业务数据（订单、文档、供应商、询价记录）。v3 要接管这套业务，但：

- 直接让 v3 用新 schema 写一份 DB → 需要一次性大迁移 + 长停机窗口
- v3 用旧 schema → v3 的 Document/Order 分离优势无法落地
- 并行两个 DB → 数据一致性难以保证

## 决策

**v3 与 v2 读写同一个 Supabase 实例**。Schema 的每次演进走四段式：

```
t=0         +3d         +1w         +4w         +8w
 │           │           │           │           │
 │  Expand   │ Backfill  │ Double    │ Cutover   │ Contract
 │ (加新列)  │ (回填历史) │ Write     │ (v3 读新列)│ (删旧列)
 │           │           │(v2+v3 写) │           │
```

### 四段式规则

1. **Expand**（加新列）
   - Alembic migration 只加列，**不删不改**现有列
   - 新列 nullable、无非空约束、无外键约束
   - v2 / v3 都能跑

2. **Backfill**（回填历史）
   - 脚本把旧数据回填到新列
   - 带断点续跑、进度、抽样核对
   - Staging 先跑，0 不一致才跑生产

3. **Double Write**（双写）
   - v2 新代码 PR：原路径 + 新列同时写
   - 观察 3 天：自动 diff 旧列值 vs 新列值，0 漂移才进入下一段
   - v3 开始接流量

4. **Cutover**（读切换）
   - v3 读新列（不读旧 JSON / 旧列）
   - v2 保留能读新列的兼容代码
   - 观察 2 周，紧急可回滚到读旧列

5. **Contract**（删旧列）
   - 只有在 Cutover 稳定 2 周后才执行
   - Alembic migration 删除旧列 / 清理 JSON 字段
   - 不可回滚点

### 举例：Phase 3 的 Order 字段迁移

- **Expand**: `ALTER TABLE orders ADD COLUMN po_number TEXT; ADD COLUMN ship_name TEXT; ...`
- **Backfill**: `scripts/backfill_order_fields.py` 把 `Document.extracted_data.metadata.po_number` 写到 `orders.po_number`
- **Double Write**: v2 `create_or_update_order_from_document` 同时写 `extracted_data` 和新列
- **Cutover**: v3 的 `domains/orders/projection.py` 只读新列
- **Contract**: Phase 7 切流 2 周后，migration 删掉 `documents.extracted_data` 里的 `metadata/products` 子键

## 放弃的方案

### 方案 A：一次性迁移 + 停机窗口

- ❌ 需要 2-4 小时业务停机，业务方很难接受
- ❌ 出问题只能熬夜回滚
- ❌ 回滚不干净（数据可能部分迁移）

### 方案 B：v3 用独立数据库，通过 API 同步

- ❌ 增加分布式一致性问题
- ❌ 同步延迟、失败处理很复杂
- ❌ 资源翻倍

### 方案 C：Blue/Green 部署 + DB snapshot 切换

- ❌ Supabase 不易做 snapshot 级切换
- ❌ 代价大过收益

## 影响

- **所有 Alembic migration** 必须分 expand-only 和 contract-only 两个 PR
- **Phase 3 最重**（唯一一次大规模字段迁移）
- **Phase 7 cutover 2 周后**才允许跑 contract migration
- **Alembic 配置**需要支持 v2 和 v3 同时对同一 DB 跑 migration——需要统一的 migration 仓库或约定（Phase 3 决策）

## 验证

- [ ] 每次 Alembic migration 在 staging 验证向前向后兼容（v2 旧代码 + 新 schema 不崩）
- [ ] 回填脚本 100% 核对一致
- [ ] 双写 3 天 0 漂移
- [ ] Contract migration 延迟到 cutover 2 周后

## 回滚策略

任何一段都能回到上一段：
- Expand 可回滚 = 删新列
- Backfill 可回滚 = 清空新列（旧数据仍在 JSON）
- Double Write 可回滚 = v2 停止双写，v3 不接流量
- Cutover 可回滚 = feature flag 切回读旧列（旧列还在）
- Contract 不可回滚（所以必须极度慎重）
