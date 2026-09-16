# 焦距 3 — 行为 + 工作流测试 (Behavior Evals + Workflow E2E)

> **新定位**（2026-05-13 大改）：分两个子层。
> **3a：Agent 行为 evals** —— 验证 agent 推理质量，跑真 LLM，类似单元/集成的"对错"判定，但容忍非确定。
> **3b：工作流 E2E** —— 模拟用户从上传到下载的完整路径，验证真文件、真后台任务、真 SSE 流。

最后核对：2026-05-13

---

## 为什么把焦距 3 拆成 3a + 3b（重要的认知修正）

之前文档把"端到端"当一回事写，结果发现是两类不同的东西：

| 维度 | 3a 行为 eval | 3b 工作流 E2E |
|---|---|---|
| **要验证的核心** | "LLM 听懂了用户意图 + 调对了 tool + 答案合理" | "上传到下载这一长串管线全跑通了 + 文件内容对" |
| **失败的根因** | prompt 漂、tool 描述误导、模型升级降智 | 路由、后台任务、序列化、文件生成 bug |
| **断言风格** | 软（must_contain / must_call_tool / 评分） | 硬（HTTP 200 + DB 行数 + Excel 单元格值） |
| **LLM** | **必须真 LLM** | LLM 可以 scripted 或真 |
| **运行成本** | 每场景 5-15 秒，API 费 | 每场景 3-30 秒，文件 IO |
| **跑频率** | 每次 merge + nightly | 每次 merge |
| **现状** | `_line_harness.py` 已有 20 场景 | **零** |

把这两类硬塞进一个"E2E"标签，结果就是设计混乱、推荐错（之前我推 VCR 录制就是把这两类搞混的产物）。

---

## 3a：Agent 行为 evals

### 现状

**`scripts/_line_harness.py`** 是这一层的种子，已经有 20 个场景：

| 类别 | 场景数 | 验证什么 |
|---|---:|---|
| A 普通问候 | 1 | agent 能正常回话 |
| B 记忆 / 重置 | 2 | session 内记忆 + 关键词清空 |
| C tool 调用基础 | 3 | list_documents / search_masterdata / list_orders 该调时调 |
| D tag 过滤 | 1 | 文档 tag 路径走对 |
| E 输出格式 | 2 | Flex 渲染条件 |
| F web_search | 1 | 真 Serper 调用 |
| G HITL | 1 | propose_action 走对 |
| H 错误处理 | 1 | 工具失败不抛 500 |
| I 跨语言 | 1 | 中→日 |
| J 链式 | 1 | 多步推理 |
| K **计数 + 聚合回归** | 4 | K1-K4，含 BLUEBERR 3500 |

**断言风格**：`must_call_tool`、`must_contain`、`must_not_contain`、`must_call_tool_any_of`、`must_contain_any_of`。**已经是粗糙版的 LLM eval**。

**调用**：
```bash
.venv/bin/python scripts/_line_harness.py --scenarios       # 全跑
.venv/bin/python scripts/_line_harness.py --only K4_*       # 单场景
```

**不在 CI**。手动跑。

### 缺口（这一层的真实差距）

| 缺什么 | 影响 |
|---|---|
| **不在 CI** | prompt / 模型升级造成的退化，要等用户报错 |
| **只有 LINE 路径** | web `/api/chat/sessions/messages` 完全没场景 |
| **断言只有 pass/fail，没有评分** | 答案 "差一点但能用" 和 "完全错" 在结果上一样 |
| **没有基线比较** | 不知道这次比上次好还是差 |
| **没有错误模式分类** | 失败时不知道是 "调错 tool"、"调对 tool 但说错话"、"模型放弃"，要人肉看日志 |

### 该做什么（按 ROI 排）

**P0：接进 CI（最重要的一步）**

```yaml
# .github/workflows/ci.yml 新增 job
behavior-eval:
  runs-on: ubuntu-latest
  if: github.event_name == 'push' && github.ref == 'refs/heads/main'
  steps:
    - run: .venv/bin/python scripts/_line_harness.py --scenarios
      env:
        GOOGLE_API_KEY: ${{ secrets.GOOGLE_API_KEY }}
        SERPER_API_KEY: ${{ secrets.SERPER_API_KEY }}
```

**只在 push 到 main 后跑**，不在 PR 阶段跑（PR 跑成本高 + flaky）。挂了报警。

**P0：补 web 端的对应场景**

复制 `_line_harness.py` 思路，写 `scripts/_chat_eval.py`，跑 `/api/chat/sessions/messages`，覆盖：
- 同样 20 个场景（用 web 接口）
- web 特有：Markdown 渲染 / 长答案 / SSE 流接收

**P1：升级到结构化 eval 框架**

考虑接 [promptfoo](https://promptfoo.dev/) 或 [Braintrust](https://braintrust.dev/)：
- 场景定义 YAML 化
- 自动跑基线比较（"这次 18/20 比上次 19/20 退步")
- LLM-as-a-Judge 给"答案质量"打分（不只 pass/fail）
- 失败分类（categorical）

**先用现有 harness 跑半年**，看真实出现的退化模式，再决定是否值得引入框架。

**P2：把现有 20 个场景扩到 50+**

每发现一个 prompt 引起的 bug，**就加一个场景**（K4 就是这么来的）。**所以场景数会自然增长，不要一次性设计完**。

---

## 3b：工作流 E2E

### 现状

**完全空白**。`tests/e2e/` 只有 `__init__.py`。

唯一沾边：8 个 `scripts/_smoke_*.py` 脚本（孤儿，不在 CI）。

### 这一层该测的是

按用户工作流分类。**每条都从 0 起步**，第一批 5 条 P0：

#### P0 工作流（用户最痛的 5 条）

| ID | 工作流 | 关键验证点 | 估时 |
|---|---|---|---:|
| **W1** | 真 Excel 邮轮订单 → `/api/orders/upload` → 等 5s → GET 详情 | DB OrderProduct 行数 = 文件商品数；多数 supplier_id 已匹配 | 5-10s |
| **W2** | 真 PDF 订单（数字版）→ 解析 → 字段提取 | metadata（PO_number / vessel / delivery_date）对得上 | 10-20s |
| **W3** | Order → POST /generate-inquiry → 监听 SSE → 下载 .xlsx → openpyxl 读回 | H8 (delivery_date) / I22 (unit) / F (pack_size) / C (product_code) 都对 | 15-30s |
| **W4** | Order → POST /financial-analysis target=USD → 双币金额 | USD = 原币 × 实时汇率 ± 1% | 3-5s |
| **W5** | Chat: propose update_product_financial → POST /actions/{id}/decide approve → 数据库变 | products.price 改了 + changelog 写入 | 5-10s |

#### P1 工作流（边界 + 历史 bug 重灾区，5 条）

| ID | 工作流 |
|---|---|
| W6 | 扫描件 PDF（图像）→ OCR fallback → 提取 |
| W7 | 5 种格式（xlsx/pdf/docx/csv/jpg）→ 每种 doc_type 正确 |
| W8 | Order → 改 country/date → /rematch → 匹配结果重算 |
| W9 | 单 supplier 重生 inquiry → 只动那一份 .xlsx |
| W10 | master-data Excel → preview → commit → products 表 N 行变化 + 可 rollback |

#### P2 防御深度（4 条）

| ID | 工作流 |
|---|---|
| W11 | 损坏 / 空 / 超大文件 → graceful 失败 |
| W12 | 跨用户：employee 看不到 superadmin 的 order |
| W13 | JWT 过期 → 自动 refresh |
| W14 | 未绑定 LINE 用户 → 收到 bind URL |

### 实施前要解决的工程问题

| 问题 | 选项 | 推荐 |
|---|---|---|
| **样本文件** | A) 不存（每次合成）<br>B) `tests/fixtures/samples/` 提交脱敏后真订单 | **B**（W1-W3 / W6 / W7 都依赖真样本） |
| **DB** | A) SQLite in-memory<br>B) Docker postgres + alembic | **B**（W10 涉及 commit/rollback，要测真 transaction） |
| **LLM 调用** | A) 真 Gemini<br>B) Scripted | **B**（工作流测的是管线不是 LLM 质量）<br>注：W2 / W6 真提取部分**例外**，挂在 3a behavior eval 那边跑 |
| **何时跑** | A) 每次 PR<br>B) merge 到 main<br>C) nightly | **A 跑 P0，B 跑 P1，nightly 全跑** |
| **后台任务怎么测** | A) 用真 asyncio<br>B) 同步跑 | **A**（W3 必须真 SSE） |

---

## 当前焦距 3 总缺口一览

```
3a 行为 eval
├── 现有 20 场景（LINE）  ✓ 但不在 CI
├── 接进 CI                  ✗
├── 写 web 版 harness         ✗
└── 结构化 eval 框架（可选）   ✗

3b 工作流 E2E
├── 14 个候选场景全部缺
├── 共享 fixture（样本文件）   ✗
├── Docker postgres setup     ✗
└── tests/e2e/conftest.py     ✗
```

---

## 该补的（焦距 3 维度，按 ROI）

### 这一轮先做的最小集（约 1-2 周工作量）

| 顺序 | 动作 | 工时估计 |
|---|---|---:|
| 1 | 把 `_line_harness.py --scenarios` 接进 GitHub Actions（push to main 后跑） | 1-2 小时 |
| 2 | 收集 5-10 个脱敏样本文件进 `tests/fixtures/samples/` | 半天 |
| 3 | 写 `tests/e2e/conftest.py`（Docker postgres + 样本文件加载） | 1 天 |
| 4 | 写 W1（订单上传 → 落库 → 匹配） | 半天 |
| 5 | 写 W3（inquiry 生成 → 下载 → openpyxl 验内容） | 1 天 |
| 6 | 写 W4（汇率换算） | 半天 |
| 7 | 写 W5（HITL approve → 执行） | 半天 |
| 8 | CI 集成：PR 跑 W1/W3/W4/W5 | 半天 |

**做完这 8 步**，你说的"上传 → 解析 → 匹配 → 汇率 → 询价单 → AI 修改"工作流就**端到端有自动化保障**了。

### 后续（不在这一轮）

- W2 / W6（真 LLM 提取）放进 3a 用 eval 风格
- W7 / W8 / W9 等 P1
- 结构化 eval 框架（promptfoo）
- 部署后 smoke 检查（focal 4，不在这三份文档范围）

---

## 维护原则（新）

1. **每次 prompt 改动 → 跑一次完整 3a**（手动或 nightly），看场景退化没
2. **每次 W1-W5 挂掉 → 优先级 P0 立修**，因为这意味着用户已经做不了事
3. **不要让 3a 和 3b 互相替代**：3a 测"agent 想得对吗"，3b 测"管线接得通吗"，挂了根因完全不同
4. **新增 prompt-driven 行为 → 加 3a 场景，不要写单元测试** —— K4 是范式
5. **新增"用户做某件事的流程" → 加 3b 工作流**，不要拆成 5 个集成测试拼起来
