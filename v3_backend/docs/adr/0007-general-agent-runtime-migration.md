# ADR-0007: Agent runtime delegated to `general-agent`

**日期**: 2026-04-27
**状态**: Accepted
**关联 PLAN**: Phase 6.1 (B-0 → B-7)

## 背景

v3 第一版自己实现了一套 ReAct agent 引擎：
- `agent/engine/` — Agent loop, registry, storage protocol
- `agent/middlewares/` — guardrail / loop-detection / memory hooks
- `agent/llm/` — Gemini SDK 直连 + LLMConfig + MockProvider
- `agent/factory.py` — composition root
- `agent/tools/*` — 闭包风格的工具 (`register_xxx(registry, ctx)`)
- `agent/prompts/` — system prompt builder
- `agent/skills/` — Markdown 技能扫描器

同时仓库里另一个目录 `curise_agent/general-agent/` 已经实现了一个独立、能力更全的通用 agent 框架：
- 4-layer 架构（CLI / Agent runtime / Capabilities / Safety+State）
- 内置工具集 (`web`, `notes`, `files`, `shell`, `planning`, `control`)
- 上下文压缩 (`TrimEngine`, `CompressingEngine`)
- 预算限制 + 取消 token + 错误分类
- 工具白名单 + 审批流 + 工作区隔离
- MCP 客户端 + skill loader + delegation 子 agent

两套并行维护，结果：
1. `general-agent` 的高级能力（web_search、context compression、budget tracking）v3 用不上
2. 修一个 agent bug 要在两边都改
3. v3 的 ReAct 引擎质量明显落后（无 web_search、无 cancel、无 budget、无 compaction）
4. 两套代码加起来 ~1500 行，单 agent 入口的目标完全没达成

## 决策

**v3 的 agent 运行时全部委托给 `general-agent`，v3 只保留业务层的胶水。**

### 边界

| 层 | 由谁拥有 | 职责 |
|---|---|---|
| Agent 运行时 (loop, ToolView, ToolContext, LLM, ContextEngine, Budget, Cancel) | `general-agent` | 通用、领域无关 |
| LLM 通信 | `general-agent` 的 OpenAI 兼容 client | 走 Gemini OpenAI-compat 端点 (`generativelanguage.googleapis.com/v1beta/openai/`) |
| Session / Memory 持久化 | v3 (`agent/runtime/`) | DB-backed，duck-types `general_agent.SessionStore` / `Memory` |
| 业务工具 (query_db, list_orders, generate_inquiry, ...) | v3 (`agent/runtime/tools/`) | 用 `@tool` 装饰器注册到 general-agent 的 REGISTRY |
| 业务依赖注入 (db, user_id, user_role) | v3 (`V3Deps` via `ToolContext.extras`) | 仿照 OpenAI Agents SDK 的 `RunContextWrapper[T]` |
| HTTP 端点 + 业务编排 | v3 (`apps/http/chat.py`, `agent/runtime/factory.py`) | 仍然是 v3 的责任 |

### 单一入口

v3 chat 路径只有 **一个** Agent 类：`general_agent.Agent`，通过 `agent.runtime.create_v3_chat_agent()` 工厂构造。
- 旧的 `agent/factory.create_agent()` + `ReActAgent` 全部删除
- 旧的工具闭包模式 `register_xxx_tools(registry, ctx)` 全部删除（被 `@tool` 装饰器代替）
- v3 不再维护任何 ReAct loop、Registry、Middleware、LLM 抽象

### Plug-in points

`general-agent` 的 `AgentConfig` 接受两个注入字段：
- `session_store: Any | None` — 任何 duck-types `SessionStore` 接口（create/get/load/append/replace_all/end_session/resolve_prefix）的对象。v3 提供 `V3SessionStore`（写 `v3_chat_*` 表）。
- `memory: Any | None` — 任何 duck-types `Memory` 接口（load/append/search）的对象。v3 提供 `V3Memory`（adapter over `MemoryStore`，写 `v3_agent_memories` 表）。

业务依赖通过 `ToolContext.extras["v3_deps"] = V3Deps(...)` 传入。`get_deps(ctx)` 在每个 `@tool` 处解包，类型清晰、跨用户隔离。

### 删除清单

```
agent/engine/         (核心 ReAct + Registry + Storage 协议)
agent/middlewares/    (loop_detection, memory, guardrail, base, chain)
agent/llm/            (gemini_provider, base, config, _mock)
agent/factory.py      (旧 composition root)
agent/tools/          (closure-pattern 业务工具)
agent/prompts/        (system prompt builder)
agent/skills/         (markdown skill scanner)
agent/storage/db.py   (DBStorage — replaced by V3SessionStore)

tests/unit/agent/test_engine.py
tests/unit/agent/test_middlewares.py
tests/unit/agent/test_prompts_and_skills.py
tests/unit/agent/test_registry.py
tests/unit/agent/test_storage_db.py
tests/unit/agent/test_tools.py
tests/integration/test_chat_e2e.py
tests/integration/test_chat_api.py
```

总计删除 ~1300 行 v3 自研 agent 代码。

### 新增清单

```
agent/runtime/__init__.py            (公开 API)
agent/runtime/deps.py                (V3Deps + inject_deps + get_deps)
agent/runtime/llm.py                 (Gemini OpenAI-compat LLMConfig)
agent/runtime/session_store.py       (V3SessionStore)
agent/runtime/memory_adapter.py      (V3Memory adapter over MemoryStore)
agent/runtime/factory.py             (create_v3_chat_agent)
agent/runtime/tools/__init__.py      (toolset wiring)
agent/runtime/tools/query_db.py      (read-only SQL with whitelist)
agent/runtime/tools/orders.py        (list_orders, get_order_detail)
agent/runtime/tools/inquiry.py       (generate_inquiry, regenerate_supplier_inquiry, get_inquiry_state)

tests/unit/agent/test_runtime_smoke.py    (V3Deps + V3SessionStore + V3Memory)
tests/unit/agent/test_v3_tools.py         (business tool dispatch + V3Deps)
tests/integration/test_chat_acceptance.py (6 production-readiness tests)

curise_agent/general-agent/pyproject.toml — 包化（package-dir mapping）
curise_agent/general-agent/agent/__init__.py — 29 公开 API
curise_agent/general-agent/agent/core.py — `AgentConfig.session_store`, `AgentConfig.memory` 注入字段
```

总计新增 ~1100 行，但其中 ~400 行是测试。生产代码净减少。

## 验收标准（已通过）

| # | 标准 | 状态 |
|---|---|---|
| 1 | HTTP/Agent/Storage E2E：完整 chat 流程，消息持久化到 v3_chat_messages | ✅ |
| 2 | Tool accuracy：agent 在被问到订单时正确调用 `list_orders` | ✅ |
| 3 | Cross-user isolation：user B 看不到 user A 的会话/消息 | ✅ |
| 4 | Architecture boundary：`scripts/check_arch.py` 0 violations + 无任何 legacy import | ✅ |
| 5 | Context compression：长对话触发 compaction、resume 看到压缩后的形态 | ✅ |
| 6 | Multi-tool：agent 串联 `web_search` (general-agent 内置) + `query_db` (v3 业务) 完成复合任务 | ✅ |

参见 `tests/integration/test_chat_acceptance.py`。

## 后果

### 正面

1. **Agent 能力立刻升级**：web_search、web_fetch、todo、remember/recall、context compression、budget tracking、cancel token —— v3 chat 路径自动获得，无需自己写。
2. **维护面缩小**：bug 在 `general-agent` 修一次，v3 自动受益；v3 不再管 LLM client / retry / message shape / tool schema 这些通用问题。
3. **类型安全更好**：业务依赖通过 `V3Deps` dataclass 携带，比之前 `ctx.db / ctx.user_id / ctx.user_role` 的松散字段访问更明确。
4. **架构边界清晰**：`agent/runtime/` 是唯一允许在 agent 包内访问 DB 的子模块（除已有的 `agent/storage/`、`agent/memory/`）；ADR-0006 RULE-2 同步更新到检查 `agent/runtime/tools/`。
5. **单一 agent 入口**：用户的 "保持一个agent入口" 要求达成。

### 负面

1. **跨仓库依赖**：v3 现在 `pip install -e ../general-agent`。dev 配置文档需要更新。
2. **Gemini 走 OpenAI-compat**：失去 `thinking_budget` 等 Gemini-only 特性。原生 `google-genai` SDK 仍用于一次性任务（PO 抽取、PDF schema 分析），不受影响。
3. **package-dir mapping**：`general-agent/agent/` 被映射成 `general_agent` 导入名，避免与 v3 的 `agent/` 命名冲突。这个 mapping 在 `general-agent/pyproject.toml`，不影响 v3。

## 实施记录

8 个阶段全部完成（journal Entry 12）：
- B-0 包化（pyproject + 公开 API）
- B-0.5 小样验证（V3Deps + V3SessionStore e2e）
- B-1 扩展点（Memory injection point）
- B-2 LLM 走 Gemini OpenAI-compat
- B-3 V3SessionStore + V3MemoryStore 完整实现
- B-4 5 个业务工具迁移到 `@tool` 装饰器
- B-5 `apps/http/chat.py` 重写
- B-6 删除 v3 自研代码
- B-7 6 项验收测试

测试结果：306 passed (271 单测 + 35 集成/契约/迁移)，0 failed，0 skipped。
