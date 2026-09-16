# ADR-0008: HITL Approval Queue + Tier Routing for Agent Writes

**日期**: 2026-04-27
**状态**: Accepted
**关联 PLAN**: Phase 6.2 (P0-P2 工作)

## 背景

ADR-0007 把 agent runtime 委托给 general-agent 后，v3 chat 只暴露读工具。
现在用户要求 chat agent 能：

1. 看 + 搜 + 读任意上传文档
2. 对订单 CRUD
3. 对主数据（products / suppliers / countries / ports / categories）CRUD
4. 通过 chat 触发数据上传（Excel → 主数据批量更新）

**核心问题**：让 LLM 直接执行 destructive 操作有风险（Replit Agent 删了用户生产 DB
是 2025 年最知名的事故）。但全部塞 confirm 卡片体验糟糕。

## 决策

**按"爆炸半径"分两层路由，不按 entity 类型一刀切。**

### 三条铁律

1. **Tier 划分原则**：reversible / 限自己数据 / 单行影响 → 直接执行；
   destructive / financial / cross-user / 批量 → propose_action 等用户确认。
2. **Action 名单白名单**：LLM 只能选 ACTION_DISPATCH 已注册的 action 名；
   未注册的名字（包括"写得很像"的）propose_action 会立刻拒绝。
3. **DB 强制隔离**：每个 dispatch 都重新读 user_id，不依赖 prompt 里的引导。

### Tier 映射表

| 操作 | Tier | 路径 |
|---|---|---|
| 所有 read 工具 | 1 | 直接执行 |
| `update_order` 安全字段（po_number, ship_name, delivery_date, ...） | 1 | service 直接 |
| `update_order` 财务字段（products, invoice_*, payment_*） | 拒绝 | 提示用 propose_action |
| `rematch_order` | 1 | 可逆 |
| `delete_order` | 2 | propose_action → user approve → service.delete_order |
| `update_product` 名称/品牌/产地 | 1 | service 直接 |
| `update_product` price/code/unit/supplier_id | 拒绝 | 提示用 update_product_financial |
| `update_supplier` 联系方式 | 1 | service 直接 |
| `update_supplier` 品类关联 | 拒绝 | 提示用 update_supplier_categories |
| 任何 `create_*` 主数据 | 2 | propose_action 必走 |
| 任何 `delete_masterdata` | 2 | propose_action 必走 |
| 数据上传 commit / rollback | 2 | propose_action 必走 |

### 架构组件

```
┌──────────────┐    1. propose      ┌──────────────────┐
│  agent.run   │ ─────────────────> │ propose_action   │
│  (LLM loop)  │                     │      tool        │
└──────────────┘                     └─────────┬────────┘
                                                │ 2. write
                                                ▼
                                       ┌──────────────────┐
                                       │ v3_pending_      │
                                       │   actions table  │
                                       └─────────┬────────┘
                                                 │ 3. SSE
                                                 ▼
   ┌─────────────────────────┐    4. user clicks    ┌─────────────┐
   │ Frontend ApprovalCard   │ ───────────────────> │ POST /chat/ │
   │ (renders payload)       │                       │  actions/   │
   └─────────────────────────┘                       │  {id}/decide│
                                                     └──────┬──────┘
                                                            │ 5. dispatch
                                                            ▼
                                                  ┌──────────────────┐
                                                  │ ACTION_DISPATCH  │
                                                  │ → domain service │
                                                  └──────────────────┘
```

### 八个已注册 actions

```
delete_order                 → orders_service.delete_order
update_product_financial     → masterdata_service.update_product (full schema)
create_product               → masterdata_service.create_product
update_supplier_categories   → masterdata_service.update_supplier
create_supplier              → masterdata_service.create_supplier
delete_masterdata            → entity-aware delete
commit_upload_batch          → masterdata.upload.commit_batch
rollback_upload_batch        → masterdata.upload.rollback_batch
```

新增 Tier-2 action 流程：
1. 在 `agent/runtime/tools/<file>.py` 写 `_dispatch_xxx(deps, target_id, payload)` 函数
2. `register_action(ActionSpec(name=..., target_kind=..., dispatch=_dispatch_xxx))`
3. 任何工具想用就 `propose_action(action="xxx", ...)`

不需要改 `/decide` 端点。

## 验收（已通过）

- 397 个 unit/integration 测试 + 6 个 acceptance 测试 + 1 e2e 全过
- HITL e2e（acceptance #7）：agent → propose → SSE → /decide → DB 真改了 + SSE resolved
- 数据上传 e2e：上传 Excel → agent 解析 + 预览 + propose commit → user approve → products 表写入

## 已知 Limitation（写入 backlog）

### 1. 孤儿 PendingAction 没有 GC

**问题**：用户调起 propose 但关掉 chat 标签页前没有点 approve/reject，行
永远停留在 `status="pending"`。

**目前的兜底**：
- 删除整个 ChatSession 会通过 FK CASCADE 清掉所有 pending 行
- 单独的孤儿没有清理路径

**未来方案候选**：
- (a) 定时 job：把 `created_at < now() - 7 days AND status='pending'` 标为 expired
- (b) 创建时设 `expires_at` 字段，每个 action 类型可以自带 TTL（delete_order 可能短，commit_upload 可能长）
- (c) /decide 端点在 approve 前检查"批准时间窗口"，过期则强制走 reject

**触发实现的信号**：`v3_pending_actions` 表行数过万，或者用户报错"我点的卡片找不到了"。

### 2. ACTION_DISPATCH 是进程内字典

**问题**：多 worker 部署时，每个进程独立维护 ACTION_DISPATCH。如果某 worker
还没 import 过定义某 action 的模块（unlikely，因为 chat.py 强制 side-effect
import），/decide 调用会失败。

**目前的兜底**：`chat.py` 顶部 `import agent.runtime.tools` 强制注册全部 8 个 action，
所以单进程绝不会缺。

**未来方案**：如果需要动态 actions（管理员后台增加），改成 DB-driven 注册表。
不在第一版做。

### 3. payload 没有 schema 校验

**问题**：propose_action 的 `payload` 是任意 JSON。dispatch 函数自己用 Pydantic
解析（如 ProductCreate），但解析失败发生在 approve 时刻而不是 propose 时刻 ——
也就是说"看起来已确认"的卡片可能在批准时报 schema error。

**目前的兜底**：`mark_decided(status="failed", result={"error": ...})` 把错误落
audit + SSE 推 failed 事件，前端可以提示。

**未来方案**：每个 ActionSpec 带 `payload_schema: type[BaseModel]`，propose 阶段
就用它校验，避免延迟到 approve 才发现。不在第一版做。

## 后果

### 正面
- agent 不能直接执行 destructive 操作（Replit-style 事故的最大缓解）
- 每个写操作都有 audit 行（decided_by / decided_at / result）
- 业务规则改动只在 dispatch 函数里，propose 工具本身不变
- prompt-injection 影响范围 = "用户多看到一张要拒绝的卡片"
- 添加新 Tier-2 操作不需要改 /decide 端点 + 不需要改前端

### 负面
- 每个 Tier-2 操作多一次往返（propose → user click → decide）
- 前端必须实现 ApprovalCard 才能让 Tier-2 路径可用
- 卡片堆积问题（见 Limitation #1）
