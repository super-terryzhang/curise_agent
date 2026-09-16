# 测试分段讨论记录

> 这份文档记录我们 section by section 走过的每一段测试。
> 每过一段，我把"测什么 / 防什么 bug / 覆盖文件"写下来，你确认或修改。
>
> 当前进度：**7-9 一起给齐，Section 1-6 已确认**
>
> 开始日期：2026-05-13
> 测试总数（清理后）：530（删了 144 个低价值的 mock-heavy 测试）

---

## 📋 9 段总览

| # | 段名 | 状态 |
|---|---|---|
| 1 | 安全核心（签名 / 密码 / token / 绑定 / 跨用户）| ✅ 已确认 |
| 2 | 架构守门 | ✅ 已确认（大概懂） |
| 3 | 文档处理（文件类型 / 提取 / 用户 tag）| ✅ 已确认 |
| 4 | 订单领域（解析 / 匹配 / 分类）| ✅ 已确认 |
| 5 | 询价单（v5 模板 / 调度 / 状态）| ✅ 已确认 |
| 6 | Master-data（CRUD + 上传管线）| ✅ 已确认 |
| 7 | 设置 + 身份认证 + 基础设施 | 🟡 讨论中 |
| 8 | LINE 集成（unit + integration） | 🟡 讨论中 |
| 9 | Web API 集成 | 🟡 讨论中 |

**图例**：⚪ 待开始 · 🟡 讨论中 · ✅ 已确认 · 🟠 需修改

---

## Section 1 — 安全核心

> 状态：✅ 已确认 · 确认日期：2026-05-13

### 测什么

防"坏人怎么进系统、进了能不能干坏事"的一组基础测试。具体测 6 件事：

1. **HMAC 签名验证** —— LINE webhook 进来的请求是不是真的从 LINE 平台发的，不是有人伪造的
2. **密码哈希 + JWT 签发** —— 用户登录时密码怎么存、token 怎么生成
3. **JWT 过期 / 刷新** —— token 7 天后失效、refresh token 怎么轮换
4. **LINE 绑定 token** —— 一次性使用、过期失效、plaintext 不存数据库
5. **LINE 用户生命周期** —— 绑定 / 改名 / 防劫持（同一个 LINE 用户不能绑两个内部账号）
6. **跨用户数据隔离** —— 用户 A 不能读 / 写用户 B 的订单、文档、上传批次

### 防的 bug

- 有人伪造 LINE webhook 让 bot 答应他不该答应的事
- 密码用明文存数据库（合规事故）
- token 被偷了之后无法吊销
- 员工 A 通过 chat 工具调用看到 / 改了员工 B 的订单（数据泄露）
- LINE 账号被劫持（一个 LINE 用户绑定别人的公司账号）

### 覆盖文件（6 个）

```
tests/unit/test_security.py                          密码 + JWT 底层
tests/unit/apps/line/test_signature.py               HMAC 签名
tests/unit/apps/line/test_bind_token.py              bind token 生命周期
tests/unit/apps/line/test_line_user_lifecycle.py     用户绑定 / 防劫持
tests/unit/apps/line/test_event_dedup.py             webhook 重传去重
tests/unit/agent/test_cross_user_isolation.py        跨用户隔离（新建）
```

### 等你回答

- 这 6 件事，有哪个不需要测？
- 还差什么应该加进来的？

---

## Section 2 — 架构守门

> 状态：✅ 已确认（用户备注：大概懂）· 确认日期：2026-05-13

### 测什么

测的是"项目代码层之间的边界规矩"。打个比方：公司里销售部不能直接动财务部的账本，必须通过财务的服务接口。代码也一样，agent 不能直接写 SQL，必须走 domain service。

我们用一个脚本 `scripts/check_arch.py` 检查这 5 条规则，这一段就是测这个脚本本身**能正确抓到违规、不会冤枉好人**。

5 条规矩：

1. **business / shared 不能 import agent.\*** —— 业务层不依赖 agent 层
2. **agent 工具不能直接碰数据库**（`db.commit / add / query / delete / flush / execute`）—— 必须走 domain service。只有 2 个例外白名单：`query_db.py`（本来就是让 agent 写 SQL 的工具）+ `propose.py`（HITL 内部状态表）
3. **跨 domain import 只能走 service / schemas / __init__** —— 不能直接 import 别的 domain 的 models 或 repository
4. **shared/ 不能 import 业务层** —— 通用工具不依赖业务
5. **apps/line 不能 import apps/http** —— LINE 入口和 web 入口是平级的，要共享就放进 domain 或 infrastructure

### 防的 bug

- 有人偷懒在 agent 工具里写 SQL，绕过 service，导致同一个 bug 在多个工具里复制（**之前 `total = len(items)` 在 4 个工具里复制的根因**）
- domain 之间产生循环依赖
- shared 工具突然依赖某个业务 domain，导致后面没法独立部署
- LINE app 和 web app 互相 import，未来想拆服务拆不掉

### 覆盖文件（1 个）

```
tests/unit/test_arch_check.py    11 个测试，逐条规则验证守门脚本不漏抓 + 不冤抓
```

这一段就 1 个文件，**但它是整个测试套件里最重要的之一**——它防的是"测试通过但架构在烂"这种慢性病。

### 等你回答

- 这 5 条规矩你觉得够不够？有没有要加的（比如：domain 内部某些路径不能互相 import？）
- 还是直接 ✅ 进 Section 3？



---

## Section 3 — 文档处理

> 状态：✅ 已确认（用户备注：之后改 test_summarizer，先 mark 住）· 确认日期：2026-05-13

### 测什么

用户上传文件这一整套行为。用户可能上传 PDF / Excel / Word / 图片 / CSV / TXT 等等，系统要做的事：

1. **认得出是什么文件** —— 看 MIME 类型 + 扩展名，决定是否接受、归到哪个 doc_type
2. **能从文件里抠出文本** —— PDF 用 pypdfium2、Word 等用 markitdown 库、Excel 用 openpyxl
3. **能给文档自动打标签** —— 看内容关键词决定是 "purchase_order" / "invoice" / 其他
4. **用户能给文档加自定义标签** —— 而且标签要去重（"BeefSupplier" 和 "beefsupplier" 算同一个）
5. **能用 AI 给文档写摘要** —— 调 Gemini 生成 markdown 摘要 + 关键词

### 防的 bug

- 用户传了个 DOCX，系统说"不支持"（其实应该支持，是路由表写漏了）
- 用户传了个 .exe 假冒 PDF，被当成 PDF 处理（安全漏洞）
- PDF 是空的 / 损坏的，系统直接 500 崩了
- 用户加 tag "Apple" 又加 "apple"，文档列出来有 2 个 tag（数据不一致）
- 摘要功能挂了但前端看不出来（静默失败）

### 覆盖文件（6 个）

```
tests/unit/domains/document/test_file_types.py            13 测试 · 文件类型路由
tests/unit/domains/document/test_markitdown_extractor.py  14 测试 · DOCX/CSV/JSON/XML/MD/TXT 提取
tests/unit/domains/document/test_extraction.py             7 测试 · PDF/Excel 提取
tests/unit/domains/document/test_classifier.py             6 测试 · 文档类型自动分类规则
tests/unit/domains/document/test_user_tags.py             13 测试 · 用户标签 CRUD + 去重
tests/unit/domains/document/test_summarizer.py             9 测试 · AI 摘要（⚠️ 需修）
```

### ⚠️ test_summarizer.py 之前标记为 MODIFY

里面 9 个测试中：
- 2 个太浅（只 assert "返回 None"，没真验证东西）
- 4 个 mock Gemini 返回硬编码 JSON，等于测自己写的 mock
- 真正缺的：没测 prompt 模板构造（长 markdown 怎么截断 + 截断提示文案）

**建议改动**：删 2 个浅测，4 个错误路径合并成一个 `@pytest.mark.parametrize`，新加 1 个 prompt 模板测试。

### 等你回答

1. 这 5 件事覆盖够吗？还有别的文档处理场景要测吗？
2. `test_summarizer.py` 现在改还是 ✅ Section 3 之后再说？



---

## Section 4 — 订单领域

> 状态：✅ 已确认 · 确认日期：2026-05-13

### 测什么

用户上传一个订单文件之后，系统把里头的内容**变成可以处理的订单数据**这一整套。3 件事：

1. **订单分类规则** —— 拿到一个文档，怎么判断它是 "purchase order" 而不是 invoice / memo / 别的？看关键词 + 打分
2. **JSON 转 Order 行** —— LLM 把 PDF/Excel 解析出来一坨 JSON 后，怎么把这些字段（PO 号 / 船名 / 货币 / 交货日期 ...）正确填进数据库的 Order 表
3. **商品匹配** —— 订单里写 "Beef Tenderloin 12kg"，系统要在 1108 个 master-data 产品里找出对应的那一条，确定 country、port、currency 这些上下文

### 防的 bug

- 用户上传 invoice 被错认成 purchase order，系统照着 invoice 去生成询价单（业务流程错乱）
- LLM 提取出来的字段 ship_name / currency 没正确写进 Order 表（数据丢失）
- 匹配算法挑错产品 —— 用户填 "Beef"，系统匹到 "Beef stock 高汤"，给错供应商发询价（**金钱损失**）
- 货币 USD 但订单实际是日本港口，被系统当成美国订单（地理错配）

### 覆盖文件（3 个）

```
tests/unit/domains/orders/test_classifier_rules.py    4 测试 · PO 关键词识别 + 打分
tests/unit/domains/orders/test_projection.py         13 测试 · JSON → Order 字段映射
tests/unit/domains/orders/test_matching.py            6 测试 · 商品匹配 + 地理推断
```

**注意**：原本还有一个 `test_llm_extractor.py`（29 个测试），上次清理时删了——因为它整套 mock 了 Gemini，等于在测 mock 而不是真行为。真正测"LLM 提取得对不对"应该放到 Section E2E（焦距 3），不在这一层。

### 等你回答

1. 这 3 件事够吗？还有哪个订单处理环节漏了？
2. 直接 ✅ 进 Section 5？



---

## Section 5 — 询价单

> 状态：✅ 已确认 · 确认日期：2026-05-13

### 测什么

用户点"生成询价单"之后这整套流程。**4 件事**：

1. **模板引擎渲染** —— 把 Order 里的数据填进 Excel 的指定单元格（H8 = 交货日期、I22 = 单位、F = pack size、C = 商品码 等等）。供应商有自己模板就用，没有就用通用模板
2. **多供应商调度** —— 一个订单常常涉及 5-10 个供应商，每个供应商一份独立 Excel。一个失败别让其他全挂
3. **状态机** —— 询价单状态（pending → running → completed / failed），Inquiry / InquirySupplier 两张表的 ORM 模型 + 序列化
4. **SSE 流推送** —— 前端要实时看到"供应商 A 完成"、"供应商 B 进行中"，这要靠从后台 worker 线程往事件循环里推消息

### 防的 bug

- 模板字段填错位置（**这是 CLAUDE.md 里记录的 4 个历史 bug 全部出处** —— PO 号 / 单位 / pack_size / 交货日期都曾错过位置）
- 一个供应商的模板有问题 → 整批 10 个供应商全失败（雪崩）
- SSE 事件推不出去 → 前端无限转圈
- 同一个 inquiry 状态被错误地从 completed 改回 pending（数据库幻觉）
- 老版数据（v2 时代的）读不出来（破坏向后兼容）

### 覆盖文件（4 个）

```
tests/unit/domains/inquiry/test_template_engine.py    12 测试 · Excel 单元格填充 + 模板选择
tests/unit/domains/inquiry/test_orchestrator.py        8 测试 · 多供应商调度生命周期
tests/unit/domains/inquiry/test_state.py               8 测试 · ORM 模型 + 序列化 + 向后兼容
tests/unit/domains/inquiry/test_sinks.py               7 测试 · SSE 事件 sink + 异步队列
```

**注意**：这套测试是 v5 重写（2026-03-01）后的版本，已经清掉了 v4 的残留（`_create_inquiry_tools` / `INQUIRY_SYSTEM_PROMPT` / `run_inquiry_agent` 这些都没了）。

### 等你回答

1. 这 4 件事覆盖够吗？还有别的询价单流程要测吗？
2. 直接 ✅ 进 Section 6？



---

## Section 6 — Master-data

> 状态：✅ 已确认 · 确认日期：2026-05-13

### 测什么

Master-data 是整个系统的"参考数据库"——产品、供应商、国家、港口、分类、汇率。**所有订单匹配、询价单生成都依赖这些数据**。

测的是这两组事：

**A 组：基础 CRUD（22 测试）**

1. **产品 / 供应商 / 国家 / 港口 / 分类 的增删改查** —— 加新产品、改信息、删除、列表
2. **`{total, items}` 形状一致** —— 所有 search 接口返回真实数据库总数 + 当前页 items（这是 2026-05 的重构，K1/K2/K3 bug 的修复）
3. **外键约束** —— 删一个国家但还有港口指向它 → 拒绝删除（409）
4. **去标准化字段** —— 创建产品时自动填 country_name、category_name、supplier_name（前端不用再 JOIN）
5. **实时汇率 fetch + upsert** —— 从外部 API 拉汇率 + 更新到本地

**B 组：批量上传管线（25 测试 / 5 个文件）**

管理员上传一个 Excel 批量导入产品。流程分 5 阶段：

1. **Parse** —— 读 Excel → 进 staging 表。处理 "$1,250.75" 这种字符串价格、缺列 / 类型不对的容错
2. **Resolve** —— 把每行 staging 数据匹配到现有产品。code 精准 = 1.0、name 精准 = 0.95、模糊匹配按相似度、低于阈值标记为"新增"
3. **Preview** —— 把变更分组显示（"会新建 200 个、更新 50 个、跳过 10 个"），让用户确认
4. **Commit** —— 原子写入 products 表 + 给每个字段变化写一条 changelog（用于回滚）
5. **Rollback** —— 通过 changelog 回滚：删掉新建的、恢复改过的字段

### 防的 bug

- 列表返回数字不对（K1/K2/K3 那种"1108 个产品被报告成 1 个"）
- 上传 commit 没写 changelog → 后悔了回不去
- Rollback 恢复不完整 → 永久数据丢失
- 跨用户：员工 A 看到员工 B 待上传的批次（隐私 / 数据泄露）
- 价格 "0" 被当成 None（**之前真的发生过**，CLAUDE.md 记录的 bug）
- 删国家时没检查港口引用 → 数据库脏数据
- 实时汇率 fetch 静默失败 → 询价单用旧汇率算钱

### 覆盖文件（6 个）

```
tests/unit/domains/masterdata/test_service.py                22 测试 · A 组：CRUD
tests/unit/domains/masterdata/upload/test_parse.py            7 测试 · 上传阶段 1
tests/unit/domains/masterdata/upload/test_resolve.py          6 测试 · 上传阶段 2
tests/unit/domains/masterdata/upload/test_preview.py          4 测试 · 上传阶段 3
tests/unit/domains/masterdata/upload/test_commit.py           5 测试 · 上传阶段 4
tests/unit/domains/masterdata/upload/test_rollback.py         3 测试 · 上传阶段 5
```

### 等你回答

1. 这两组事覆盖够吗？还有别的 master-data 流程要测吗？
2. ✅ 进 Section 7？



---

## Section 7 — 设置 + 身份认证 + 基础设施

> 状态：🟡 讨论中 · 提议日期：2026-05-13

### 测什么

把"用户管理 + 系统配置 + 一些底层基础设施"放一起。**5 件事**：

1. **登录认证全流程** —— bcrypt 密码哈希、JWT 签发 / 刷新 / 撤销、5 次失败锁账户、改密码后撤销所有 session
2. **系统配置 CRUD** —— field schemas（字段定义）、订单模板、供应商模板、交货地点、公司信息
3. **模板自动分析** —— 上传一个新模板时，启发式识别它属于哪家公司（"ROYAL CARIBBEAN" 关键词 → 自动归类）
4. **本地文件存储** —— LocalFileStorage 的读写 / 路径消毒（防 `../` 穿越）/ signed URL 生成
5. **Agent 记忆 + 询价工具权限** —— Agent 的 remember/recall 记忆存储 + inquiry 工具的跨用户权限检查

### 防的 bug

- 登录失败 5 次后没锁账户 → 暴力破解
- 改密码后旧 session 还能用（应该撤销）
- 用户上传文件名带 `../../../etc/passwd` 没被消毒 → 服务器文件泄露
- 不同用户的 agent 记忆混在一起（隐私 / 数据泄露）
- employee 通过 agent 给自己生成别人订单的询价单（权限漏洞）

### 覆盖文件（6 个）

```
tests/unit/domains/identity/test_service.py            14 测试 · 登录认证全流程
tests/unit/domains/settings/test_service.py            10 测试 · 系统配置 CRUD
tests/unit/domains/settings/test_analyze.py             5 测试 · 模板自动分析
tests/unit/test_local_storage.py                        5 测试 · 本地文件存储
tests/unit/agent/test_memory_store.py                   7 测试 · Agent 记忆存储
tests/unit/agent/tools/test_inquiry_auth.py             3 测试 · Inquiry 工具权限
```

---

## Section 8 — LINE 集成（unit + integration）

> 状态：🟡 讨论中 · 提议日期：2026-05-13

### 测什么

LINE Messaging API 相关的一切。Section 1 把安全部分（签名 / bind token / 防劫持 / 去重）已经覆盖了，这一段是**剩下的"业务行为"**部分。**4 件事**：

1. **Flex Message 渲染** —— 把表格 / 列表 / KV 对转成 LINE Flex JSON 卡片。35 个测试覆盖每种数据形状
2. **输出路由** —— Agent 回复是 markdown 表格 → 走 Flex；普通文本 → 走 text 气泡；超长 → 切分
3. **Session 计时 + 重置关键词** —— 30 分钟 idle 切会话 / "新对话/重置/reset" 关键词清空
4. **绑定 URL 构造** —— 拼 `https://app/line/bind/{token}`，处理斜杠 / encoding 等边界
5. **Webhook DM 处理**（集成层）—— 收到消息后调对 agent、未绑定 → 发 bind URL、被 blocked 用户静默丢弃、图片 / 文件 → 礼貌拒绝
6. **群聊 / Webhook 去重**（集成层）—— 群消息 Phase L1 拒绝、重传事件去重

### 防的 bug

- Flex JSON 形状错（长字段截断不对 / 中文挤一坨）→ 用户手机上看不清
- markdown 表格没被检测出来，结果发了一坨没格式的文字
- 用户 31 分钟后回来问"刚刚说什么"被切了 session → bot 说不记得（已 review，30 min 这个阈值有争议但暂保留）
- LINE 重发 webhook 没去重 → bot 回 2 次相同的消息
- 群聊里 bot 答应了不该答应的事

### 覆盖文件（8 个）

```
tests/unit/apps/line/test_flex_renderer.py             35 测试 · Flex JSON 生成
tests/unit/apps/line/test_delivery.py                  18 测试 · markdown → flex/text 路由
tests/unit/apps/line/test_session.py                   12 测试 · idle / reset
tests/unit/apps/line/test_identity_url.py               4 测试 · bind URL 构造
tests/integration/line/test_bind_endpoint.py            8 测试 · 绑定 API 完整流程
tests/integration/line/test_webhook_dm.py              11 测试 · DM 收发 ⚠️ 全 mock fake_platform
tests/integration/line/test_webhook_dedup_and_group.py  4 测试 · 群聊拒绝 + 去重
tests/integration/line/test_webhook_signature.py        5 测试 · 签名验证 HTTP 层
```

**注意**：`test_webhook_dm.py` 之前审查标记为 AT_RISK（断言全在 fake_platform.replies 而非真 HTTP 响应），保留但有这个局限。

---

## Section 9 — Web API 集成（HTTP 层）

> 状态：🟡 讨论中 · 提议日期：2026-05-13

### 测什么

所有 FastAPI 路由的 **HTTP 契约**——端点存在、状态码对、JSON 形状对、RBAC 拦得住。**这一段是焦距 2（集成层）的核心**。

按业务域分组：

**A 组：身份认证**
1. `test_auth_api.py` (10) —— 登录 / 刷新 / 登出 / /me / 改密码
2. `test_users_api.py` (9) —— 用户 CRUD（superadmin only）/ 锁账户 + token 撤销 / 重置密码

**B 组：文档 + 文件**
3. `test_documents_api.py` (14) —— 上传 / 列表 / 详情 / tag CRUD / order-payload / 跨用户隔离
4. `test_files_api.py` (7) —— `/uploads/{key}` 静态服务 + 路径穿越防护

**C 组：订单 + 询价**
5. `test_orders_api.py` (13) —— 上传 / 列表 / patch / rematch / anomaly / review / delete
6. `test_inquiry_api.py` (8) —— readiness / generate / preview / cancel / 单 supplier 重生

**D 组：主数据 + 设置**
7. `test_masterdata_api.py` (11) —— 国家 / 港口 / 供应商 / 产品 / 汇率 列表
8. `test_settings_api.py` (16) —— field-schemas / 模板 / 公司配置 / 交货地点
9. `test_data_upload_api.py` + `test_data_upload_e2e.py` (各 1) —— **要合并**，见待修清单

**E 组：AI Agent + HITL**
10. `test_chat_acceptance.py` (7) —— Chat session + agent 跑 scripted LLM ⚠️ AT_RISK
11. `test_propose_action_api.py` (6) —— HITL approve / reject / 跨用户拒绝 ⚠️ AT_RISK（没测真 dispatch 链路）

### 防的 bug

- 端点存在但 RBAC 没接上 → employee 调了 superadmin 的接口（数据泄露）
- 改了 schema 但前端调用还按老格式 → 集成 500
- 跨用户访问没拦（employee A 拿 user_id=B 拉别人的订单）
- HITL approve 之后没真的 dispatch 到 service（按钮假装生效）
- 撤销用户后 refresh token 还能用（应该一起失效）

### 覆盖文件（11 个 / 后续合并后 10 个）

```
tests/integration/test_auth_api.py             10
tests/integration/test_users_api.py             9
tests/integration/test_documents_api.py        14
tests/integration/test_files_api.py             7
tests/integration/test_orders_api.py           13
tests/integration/test_inquiry_api.py           8
tests/integration/test_masterdata_api.py       11
tests/integration/test_settings_api.py         16
tests/integration/test_data_upload_api.py       1   ⚠️ 待合并
tests/integration/test_data_upload_e2e.py       1   ⚠️ 待合并
tests/integration/test_chat_acceptance.py       7   ⚠️ AT_RISK
tests/integration/test_propose_action_api.py    6   ⚠️ AT_RISK
```

### 已知缺口（从之前焦距 2 文档迁移）

| ID | 漏测端点 | 严重度 |
|---|---|---|
| G1 | `POST /orders/{id}/financial-analysis` | 🔴 高 |
| G2 | `POST /chat/actions/{id}/decide` 真 dispatch 链路 | 🔴 高 |
| G3 | `POST /orders/{id}/reprocess` | 🔴 中 |
| G4 | `GET /orders/{id}/file-preview` | 🔴 中 |
| G5 | `POST /api/excel/parse` + `/parse-cells` | 🟡 中 |
| G6 | `documents/{id}/create-order` | 🟡 中 |
| G7 | SSE 端点 (`/chat/stream`, `/inquiry-stream`) | 🔴 高（属焦距 3）|

---



---

## 🔖 待修清单（讨论时 mark 住的事，最后一起做）

| 来自 | 内容 | 工时估计 |
|---|---|---|
| Section 3 | 改 `test_summarizer.py`：删 2 个浅测、合并 4 个错误路径成 parametrize、新加 1 个 prompt 模板测试 | ~30 分钟 |
| 早前审查 | 合并 `test_data_upload_api.py` + `test_data_upload_e2e.py` → `test_data_upload_pipeline.py` 并补错误用例 | ~30 分钟 |
| 早前审查 | `test_flex_renderer.py` 35 个测试里有 7 个是重复变种，可以合并到 27 个 | ~20 分钟（可后排）|
