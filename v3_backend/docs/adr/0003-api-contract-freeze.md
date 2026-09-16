# ADR-0003: API 合约冻结为 v2 当前状态

**日期**: 2026-04-24
**状态**: Accepted
**关联 PLAN**: ADR-3

## 背景

v2 的前端（`v2-frontend/src/lib/*-api.ts`）用硬编码的 URL 和 JSON 字段名访问约 100 个 `/api/*` 端点。v3 是**后端内部重构**，不是接口重设计。

如果 v3 任何端点的 URL / HTTP 方法 / 请求 shape / 响应 shape 与 v2 不一致：
- 前端立刻挂
- 无法做"前端零改动切流"
- 灰度时 v2/v3 并存会出现诡异的兼容问题

## 决策

v3 的 HTTP 对外契约 **100% 兼容 v2 当前状态**，不做任何破坏性变更。

### 冻结对象

以下四件事不允许改：
1. URL 路径（`/api/auth/login`, `/api/orders/{id}/rematch`, ...）
2. HTTP 方法（GET / POST / PATCH / PUT / DELETE）
3. 请求体字段名和类型（如 `{"email": str, "password": str}`）
4. 响应体字段名和类型（如 `{"access_token": str, "refresh_token": str, ...}`）

### 允许变化的

- 响应时间（v3 可以更快）
- 日志格式
- 错误详细信息（前提是 HTTP 状态码一致）
- 内部实现（SQL、缓存、异步策略等）

### 落地机制

1. **Phase 0 录制 v2 合约**
   - `scripts/snapshot_v2_openapi.py` 导出 `docs/api-contract.yaml`（OpenAPI 3.0）
   - `scripts/record_v2_responses.py` 对固定 fixture 录响应 → `tests/contract/v2_baseline.json`

2. **合约测试**
   - `tests/contract/test_*.py` 读 baseline，打同样请求到 v3，对比响应
   - 忽略字段白名单：`created_at`, `updated_at`, `token`（非确定性字段）
   - CI 阻塞：合约测试红灯 → PR 不能合并

3. **Shadow Mode（Phase 7 前）**
   - 生产流量镜像到 v3-staging，实时对比响应
   - 差异报告每日出，修到 0 才切流

### 例外处理（极少）

如果发现 v2 的某个端点有 bug（比如字段拼写错误），v3 **保留**原 bug。修 bug 走单独的 v4 规划，不能在重构里夹带。

## 放弃的方案

### 方案 A：v3 设计更合理的新接口，前端同步改

- ❌ 前端 100+ 调用点要改
- ❌ 灰度期间新旧接口并存，前端更复杂
- ❌ 重构和接口设计两件事混在一起，风险叠加

### 方案 B：后端版本化（/api/v2/..., /api/v3/...）

- ❌ 前端要区分访问哪个版本
- ❌ 合约稳定应由契约测试保证，不由 URL 版本
- ❌ 增加运维复杂度

## 影响

- **Phase 0 必须做的事**：
  - 录制 v2 OpenAPI
  - 录制 v2 响应 JSON（至少覆盖核心 50 个端点）
- **每个 Phase 的 DoD**：该阶段涉及的端点合约测试 100% 通过
- **Phase 7 切流前**：Shadow Mode 跑满 5 天无异常

## 验证

- [ ] `docs/api-contract.yaml` ≥ 95 个端点
- [ ] `tests/contract/v2_baseline.json` ≥ 50 个请求-响应样本
- [ ] CI 的 contract job 阻止 shape 改变
