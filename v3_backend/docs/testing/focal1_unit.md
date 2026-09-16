# 焦距 1 — 单元测试 (Unit Tests)

> **新定位**（2026-05-13 修订）：只测**确定性纯函数**和**不依赖外部状态的小类**。
> 任何"我们把 LLM mock 了来测系统行为"的测试都应该在焦距 3 用真 LLM 跑 eval，不写在这一层。

最后核对：2026-05-13

---

## 这一层的目的

> **防止加减乘除算错。** 一个函数给定输入 X，应该返回 Y。
> 极快（毫秒级），完全隔离，不碰真 DB / 真 LLM / 真 HTTP / 真文件。

**这一层不该承担的事**：
- 验证 agent 推理对不对（→ 焦距 3）
- 验证 LLM 提取的字段对不对（→ 焦距 3）
- 验证 API 端点接好了没（→ 焦距 2）
- 验证一个工作流端到端是否走通（→ 焦距 3）

---

## 现状（**476 个测试，46 个文件**）

- 位置：`tests/unit/`
- 框架：pytest + pytest-asyncio
- DB：SQLite in-memory，`Base.metadata.create_all()` 建表
- LLM：默认 mock（`tests/conftest.py:54-62` 清空 `GOOGLE_API_KEY`）

---

## 按"是否名副其实"重新分类

### ✅ 名副其实（真的是单元测试，~280 测试）

这些测纯函数 / 数据结构 / 算法。**继续维护，新加同类测试欢迎进来。**

| 类别 | 文件 | 测试数 | 性质 |
|---|---|---:|---|
| 安全工具 | `tests/unit/test_security.py` | 11 | bcrypt + JWT 签名 |
| 本地存储 | `tests/unit/test_local_storage.py` | 5 | 文件读写 + 路径消毒 |
| 架构守门 | `tests/unit/test_arch_check.py` | 11 | check_arch 的 5 条规则 |
| 文件类型路由 | `tests/unit/domains/document/test_file_types.py` | 13 | MIME → doc_type 映射 |
| User tag 规范化 | `tests/unit/domains/document/test_user_tags.py` | 13 | 字符串清洗 |
| 订单分类规则 | `tests/unit/domains/orders/test_classifier_rules.py` | 4 | 关键词打分 |
| Order projector | `tests/unit/domains/orders/test_projection.py` | 13 | Document JSON → Order 字段 |
| 订单匹配算法 | `tests/unit/domains/orders/test_matching.py` | 6 | 国家/港口/币种解析 |
| Masterdata 服务 | `tests/unit/domains/masterdata/test_service.py` | 22 | search 返回 `{total, items}` 形状 |
| Upload pipeline 各阶段 | `tests/unit/domains/masterdata/upload/*.py` | 25 | parse / resolve / preview / commit / rollback |
| Inquiry 状态机 | `tests/unit/domains/inquiry/test_state.py` | 8 | 状态机转换 |
| Inquiry sinks | `tests/unit/domains/inquiry/test_sinks.py` | 7 | SSE event sink 数据结构 |
| Inquiry 模板引擎 | `tests/unit/domains/inquiry/test_template_engine.py` | 12 | 字段映射 + 公式 |
| Settings 服务 CRUD | `tests/unit/domains/settings/test_service.py` | 10 | DB CRUD |
| Identity 服务 | `tests/unit/domains/identity/test_service.py` | 14 | 登录失败计数 + 锁账户 |
| LINE 签名 | `tests/unit/apps/line/test_signature.py` | 7 | HMAC-SHA256 |
| LINE bind token | `tests/unit/apps/line/test_bind_token.py` | 8 | 生成 + 验证 + 过期 |
| LINE event 去重 | `tests/unit/apps/line/test_event_dedup.py` | 6 | record_event_id |
| LINE session 计时 | `tests/unit/apps/line/test_session.py` | 12 | idle / reset 判断 |
| LINE identity URL | `tests/unit/apps/line/test_identity_url.py` | 4 | URL 构造 |
| LINE user lifecycle | `tests/unit/apps/line/test_line_user_lifecycle.py` | 6 | LineUser CRUD |
| LINE Flex renderer | `tests/unit/apps/line/test_flex_renderer.py` | 35 | Flex JSON 生成 |
| LINE delivery 路由 | `tests/unit/apps/line/test_delivery.py` | 18 | markdown → flex vs text |
| **小计** | | **270** | |

### ⚠️ 边缘（mock 太多 / 价值偏低，**保留但不新加**）

这些"看起来是单元测试"，但因为大量 mock 了 LLM 或 HTTP，**实际验证的是 "我们调用 mock 的姿势对" 而不是 "系统行为对"**。**不删**（删了减少基础信心），但新行为请到焦距 3。

| 文件 | 测试数 | 为什么"边缘" |
|---|---:|---|
| `tests/unit/domains/orders/test_llm_extractor.py` | 29 | 全部 mock Gemini。测的是 "我们的提取器调用了 chat.completions.create"，不是 "Gemini 真能提对字段" |
| `tests/unit/domains/document/test_vision_extractor.py` | 11 | 同上，mock Vision API |
| `tests/unit/domains/document/test_summarizer.py` | 9 | 同上，mock 摘要 LLM |
| `tests/unit/domains/document/test_pdf_orchestrator.py` | 10 | 测多路由编排逻辑，OK 但跨度大 |
| `tests/unit/domains/document/test_classifier.py` | 6 | 部分用真 keyword，部分 mock |
| `tests/unit/domains/document/test_extraction.py` | 7 | 测 dispatch 路由 |
| `tests/unit/domains/document/test_markitdown_extractor.py` | 14 | 包装第三方库的薄层 |
| `tests/unit/domains/settings/test_analyze.py` | 5 | 模板分析 mock Gemini |
| `tests/unit/agent/test_runtime_smoke.py` | 19 | 起 Agent 但 mock LLM，行为推不出来 |
| `tests/unit/agent/test_v3_tools.py` | 8 | 测 query_db 等具体 tool 实现 |
| `tests/unit/agent/test_toolset_layout.py` | 6 | 测 toolset 装配规则 |
| `tests/unit/agent/test_llm_streaming.py` | 7 | 测流式回调 |
| `tests/unit/agent/test_memory_store.py` | 7 | DB roundtrip |
| `tests/unit/agent/test_propose_action.py` | 7 | DB roundtrip |
| `tests/unit/agent/tools/*.py` | 53 | Agent 工具的薄壳测试，多数验证"调对了 service" |
| **小计** | | **206** | |

---

## 缺口（重新评估）

**之前文档里"该补汇率换算单测"和"该补真样本回归"两条建议，重新评估**：

| 之前的建议 | 重新评估 | 新结论 |
|---|---|---|
| `test_currency_conversion.py` 单测 | 汇率换算实质是"汇率表查询 + 乘除"，确实可以纯函数化 | **✓ 保留**，归这一层 |
| `test_real_sample_extraction.py` 真样本提取 | 这本质上是 E2E（真文件 + 真提取） | **✗ 移到焦距 3** |

---

## 该补什么（焦距 1 维度，重新评估）

**只补 1 个文件**（不再扩张这一层）：

| 优先级 | 新增文件 | 内容 | 测试数预估 |
|---|---|---|---:|
| **P0** | `tests/unit/domains/orders/test_currency_conversion.py` | JPY→USD / USD→JPY / 缺汇率降级 / 浮点精度 / 双向 rate 一致性 | 6-8 |

**移出去的**：
- "真样本提取测试" → 移到焦距 3 的工作流 E2E

---

## 维护原则（新）

1. **新加单元测试前问自己**：这个测试是否需要 mock LLM / 网络 / 文件 IO？如果需要，**很可能不属于这一层**。
2. **现有"边缘"206 个测试不删** —— 它们提供了"代码至少能 import 起来 + 函数签名没变"的烟雾信号。
3. **新行为验证写到 `_line_harness.py` 风格的 eval 场景** —— 看焦距 3 文档。
4. 单元测试**应该读起来像中学数学题**："输入 X，输出应该是 Y。" 不该有 5 行 mock 配置 + 复杂 setup。
