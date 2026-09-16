# ADR-0002: Agent 通过 Service 契约消费业务，不碰 DB

**日期**: 2026-04-24
**状态**: Accepted（目录布局由 ADR-0007 更新）
**关联 PLAN**: ADR-2

> **更新提示** (2026-04-27)：本 ADR 描述的目录 `agent/engine/`、`agent/llm/`、`agent/middlewares/`、`agent/tools/business/` 已在 Phase 6.1 (ADR-0007) 删除。Agent runtime 委托给 `general-agent`；v3 业务工具搬到 `agent/runtime/tools/`，仍遵守"只调 service、不碰 DB"的核心契约。

## 背景

v2 把 Agent 和业务双向耦合：

- `services/orders/inquiry_agent.py:34` 直接 `from services.agent.stream_queue import push_event`——业务反向依赖 Agent 的 SSE 通道
- `services/tools/data_upload.py` 等 Agent 工具直接 `db.query(Product).filter(...)` 操作 DB——业务逻辑散落在工具层
- HTTP `routes/orders.py` 和 Agent 工具 `services/tools/order_matching.py` 各自调用同一套底层函数，但代码路径分叉

结果：
- 想脱离 Agent 运行询价生成（Cron、批处理、CLI）做不到
- 想把 `data_upload` 业务逻辑复用到 HTTP 端点，要把 2699 行 tool 拆出来
- 想换 LLM provider，要改业务代码

## 决策

**Agent 和业务单向依赖：Agent → Service，不反向。**

```
┌─────────────────────────────────────────┐
│  apps/http/*.py   apps/jobs/*.py        │ ← 接入层（HTTP / 定时任务 / CLI）
└──────────┬──────────────┬───────────────┘
           │              │
           ▼              ▼
┌─────────────────────────────────────────┐
│  domains/<name>/service.py              │ ← 业务唯一入口
│  + models.py + repository.py            │
└──────────▲──────────────────────────────┘
           │
┌──────────┴──────────────────────────────┐
│  agent/tools/business/*.py              │ ← Agent 工具（调 service）
│  agent/tools/generic/*.py               │
│  agent/engine/* + agent/llm/*           │
└─────────────────────────────────────────┘
```

### 硬规则

1. **业务代码禁止 import Agent**
   - `domains/**` 不能 `from agent.*`
   - 业务需要回调（如进度事件）时，通过**注入的接口**（`Protocol` 定义），不依赖 Agent 的具体实现

2. **Agent 工具禁止直写 DB**
   - `agent/tools/**` 禁止 `db.query`, `db.commit`, `db.add`
   - 工具只调 `domains.<name>.service.xxx(...)` 并把结果格式化给 LLM

3. **Agent 基础设施禁止跨入业务**
   - `agent/engine/`, `agent/llm/`, `agent/middlewares/` 不知道有什么业务

### 进度事件通过 Protocol 注入

```python
# domains/inquiry/orchestrator.py
from typing import Protocol

class InquiryProgressSink(Protocol):
    def on_start(self, supplier_id: int) -> None: ...
    def on_progress(self, supplier_id: int, event: dict) -> None: ...
    def on_done(self, supplier_id: int, result: dict) -> None: ...
    def should_cancel(self) -> bool: ...

def run_inquiry(order_id: int, sink: InquiryProgressSink, db) -> Result: ...
```

- HTTP 层实现 `SSESink`（推到 asyncio.Queue）注入
- Agent 实现 `AgentStreamSink`（推到 agent.engine.stream）注入
- 批处理实现 `NullSink`（no-op）注入
- 业务代码永远不知道谁接收事件

## 放弃的方案

### 方案 A：Agent 和业务共用全局事件总线（如 Redis Pub/Sub）

- ❌ 引入额外基础设施
- ❌ 调试困难（事件消费方不透明）
- ❌ Phase 5 不需要

### 方案 B：把 Agent 当作 "library" 在业务内调用

- ❌ 业务要感知 LLM provider、prompt、中间件
- ❌ 业务单元测试被 LLM 非确定性污染
- ❌ 加重业务代码体积

## 影响

- **Phase 5 重构 inquiry orchestrator** 时必须移除所有 `from agent.*`
- **Phase 6 Agent 工具全部重写**，业务逻辑下沉到 `domains/<name>/service.py`
- **Phase 0 CI arch-check 脚本**自动阻止违规 import

## 验证

- [ ] `grep -rE "^from agent" domains/` 返回 0 匹配
- [ ] `grep -rE "db\.(commit|add|query)" agent/tools/` 返回 0 匹配（白名单：`agent/storage/`, `agent/memory/`）
- [ ] CI arch-check 阻断违规 PR
