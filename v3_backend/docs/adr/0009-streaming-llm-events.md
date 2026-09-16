# ADR-0009: Token-level streaming for chat (LLM + SSE)

**日期**: 2026-04-30
**状态**: Accepted
**关联**: ADR-0007 (general-agent runtime), ADR-0008 (HITL)

## 背景

ADR-0007 把 v3 chat 委托给 general-agent，但 general-agent 的 `LLM.complete()`
是阻塞调用 — 跑完整个 chat completion 才返回。这导致 v3 chat 的 SSE 只有
4 个事件类型（`run_started` / `run_completed` / `run_error` / `approval_*`），
LLM 输出的内容一次性到 DB，前端只能在 `run_completed` 后整体 fetch。

体感问题：
- 用户发完消息后是几秒钟的"思考中..."，然后 reply 突然出现 — 焦虑感强
- assistant-ui 风格的 ToolGroup / Reasoning 组件本身为 streaming 设计的（auto-expand-during-streaming），不流式 = 这些组件的设计意图浪费
- 长 reply（500+ 字）等待时间感受是几倍长

## 调研

研究了 hermes-agent (`run_agent.py` ~10K 行 production-grade ReAct agent +
`gateway/stream_consumer.py`)，找到了行业级 streaming 的标准做法：

1. **OpenAI-compat `stream=True`** — 同 chat.completions.create 但返回 iterator
2. **多 callback 分流** — text / reasoning / tool-call-started / tool-args 各自的 hook
3. **tool-text 抑制** — 当 turn 有 tool_calls 时不推 content delta（避免
   "Let me check..." 这种 chatty 文本噪音）
4. **reasoning 分流** — `delta.reasoning_content`（DeepSeek R1 / OpenAI o-series）
   走单独 callback，不混进主文本流
5. **同步→异步桥** — agent worker thread 同步触发 callback，async layer
   thread-safe 转发到 UI 队列
6. **rate-limit 渲染** — 不要每个 token setState；buffer 50ms 或 30 字符

详见 v3_backend AGENT_JOURNAL Entry 14 的研究记录。

## 决策

**完整移植 hermes 的 streaming 模式到 general-agent + v3**。

### 三层架构

```
┌──────────────────────────────────────────────────────────────┐
│ general-agent/agent/llm.py                                   │
│   StreamCallbacks dataclass:                                 │
│     on_text_delta(text)                                      │
│     on_reasoning_delta(text)                                 │
│     on_tool_call_started(call_id, tool_name)                 │
│     on_tool_args_delta(call_id, args_delta)                  │
│     on_assistant_message_done()                              │
│   LLM.complete(stream_callbacks=...) 切到 stream=True 路径    │
│   _complete_streaming(): chunks → 累积 + 触发 callbacks      │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ v3_backend/apps/http/chat.py                                 │
│   _run_agent_blocking(...) registers callbacks 把每个事件    │
│   通过 _chat_streams.handle.emit() 推入 SSE queue             │
│   也注册 on_step（Agent-level）→ tool_result 事件             │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ v3-frontend/src/lib/v3-chat-store.ts                         │
│   streamSession() 收到事件 → 增量 setState：                   │
│     text_delta → 拼到 in-flight assistant.content             │
│     tool_call_started → 加 tool_call 到 in-flight assistant   │
│     tool_args_delta → 累积 args                              │
│     assistant_message_done → 封 in-flight，下个 delta 起新的  │
│     tool_result → 加 tool 消息                               │
│   run_completed → fetch 一次 backend canonical messages 重置  │
└──────────────────────────────────────────────────────────────┘
```

### SSE 事件类型 (新增 6 个)

```
text_delta             {delta: str}
reasoning_delta        {delta: str}
tool_call_started      {call_id, tool_name}
tool_args_delta        {call_id, delta: str}
tool_result            {tool_name, args, result}
assistant_message_done {}
```

加上原有的 `run_started` / `run_completed` / `run_error` / `run_replaced` /
`approval_request` / `approval_resolved` / `ping` / `run_idle`。

### tool-text 抑制规则

LLM client 层执行（`agent/llm.py:_complete_streaming`）— 当一个 turn 累积
出 tool_calls 时，**不再触发 `on_text_delta`**，但 content 仍累积到最终
message 里（保留给 DB 持久化和后续 context）。这样：
- DB 完整保留 LLM 输出（含 "Let me check..." 之类）
- UI 只看到结构化的 tool_calls 卡片，不会被 chatty 文本污染

### Tool result 走 on_step，不走 LLM stream

LLM 流不知道 tool result（dispatch 在 agent loop 里发生）。所以：
- LLM 推 `tool_call_started` + `tool_args_delta` + `assistant_message_done`
- agent 派发 tool → 调用 tool 函数 → 拿到 result
- agent 的 `on_step` callback (kind="tool") 推 `tool_result` 事件

这是**两条并行 channel**：LLM channel 推生成事件，agent channel 推执行结果。

## 验收

### Backend
- `tests/unit/agent/test_llm_streaming.py` 7 个单测全过：
  - text-only 流式累积顺序 ✓
  - tool_call_started 触发一次 per tool ✓
  - tool-text 抑制规则 ✓
  - reasoning 分流 ✓
  - assistant_message_done 触发 ✓
  - 多 tool_calls 各 index 独立 ✓
  - 无 callbacks 时 fall back to blocking 路径 ✓
- v3 全套 410 tests 不回归
- curl 实测：21 个 text_delta + tool_call_started + tool_result 序列正确

### Frontend
- TypeScript 0 errors
- v3-chat-store 增量 setState 路径覆盖 6 种新事件
- v3-chat-api 的 V3StreamEvent union 类型完整

## 后果

### 正面
- 用户发消息 → ~1s 内开始看到文字流出（vs 之前 ~5-15s 完全静默）
- ToolGroup 的 auto-expand-during-streaming 真正生效（之前不流式时是死代码）
- 长 reply 体感快几倍（同样的 LLM token 速度，但 streaming 让感知开始时间提前）
- 跟 ChatGPT / Claude / 业界 chat UX 一致 — 没人再问"为什么不是 SSE"

### 负面
- general-agent 的 LLM 抽象多了一条 streaming code path（增加约 130 行）
- `on_step` 复用为 tool result 通道 — 之前是 trace-only callback，现在
  也用于 SSE 推送。需要保持向后兼容（CLI 仍然能用）
- chat-store 的状态机更复杂（in-flight assistant message + sealing 逻辑）

### 不做的事（限制 scope）
- ❌ rate-limiter（50ms / 30 字符 buffer）— 现在 Gemini OpenAI-compat
  是按短句分块到的（21 个 chunks），React 19 batching 应付得了。
  **如果**未来切到 token-by-token 流（每个字符一个 chunk）再加。
- ❌ Anthropic SDK 流式 — v3 用 Gemini，现在不支持。general-agent 的
  hermes 借鉴模式可以扩展，等需要时再做。
- ❌ tool args 实时显示在 UI（args streaming 到 UI 让用户看 LLM 怎么填参数）—
  当前 ToolGroup 只在 args 完整后展开。是 nice-to-have，留给后续 polish。

## 实施记录

8 个阶段 (#79-#87)：
- A.1 (general-agent llm.py + StreamCallbacks)：~1h
- A.2 (AgentConfig.stream_callbacks + Agent.run wiring)：~30min
- A.3 (单元测试 7 个)：~45min
- B.1 (v3 backend chat.py 注册 callbacks 推 SSE)：~30min
- B.2 (rate-limiter)：跳过，defer
- B.3 (curl 实测)：~10min
- C.1 (frontend chat-store 处理 6 个新事件 + helpers)：~1h
- C.2 (MessageView 适配)：无需改动 — groupMessages 自然处理增量更新
- C.3 (e2e 验证)：~5min

**实际工作量 ~4 小时**（估 9-13h，比预想快因为之前的 MessageView ToolGroup
设计已经为 streaming 准备好了 —— "以正合，以奇胜"，前面做对的设计在这步
开花结果）。
