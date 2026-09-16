# test_v2 — 重写的测试套件

> 2026-05-13 重写完成。按"用户能做什么"组织（9 个 section + E2E），不是按"代码在哪一层"。
>
> **状态：896 个测试，0 失败。** 全部基于现行生产代码现写（不是从旧 `tests/` 迁移）。
> 旧 `tests/` 目录已废弃，可以安全删除。
>
> **每段 section 的设计目标和覆盖范围记在 `docs/testing/sections.md`**。

---

## 怎么跑

```bash
# 全部
.venv/bin/python -m pytest test_v2 -q

# 单个 section
.venv/bin/python -m pytest test_v2/section_4_orders -v

# 只跑 E2E（需要真 PDF + 一些时间）
.venv/bin/python -m pytest test_v2/section_e2e -v

# 跳过 E2E，只跑单元 + 集成
.venv/bin/python -m pytest test_v2 -q --ignore=test_v2/section_e2e
```

---

## 目录结构

```
test_v2/
├── README.md                       本文件
├── conftest.py                     全局 fixtures（DB、auth、tmp 文件存储）
├── fixtures/
│   ├── helpers.py                  make_excel / make_pdf / 样本文件加载
│   └── samples/                    真实样本订单 PDF（链接到 test-orders/）
│
├── section_1_security/             安全核心（签名 / 密码 / token / 绑定 / 跨用户）
├── section_2_architecture/         架构守门（check_arch 5 条规则）
├── section_3_documents/            文档处理（文件类型 / 提取 / 用户 tag）
├── section_4_orders/               订单领域（解析 / 匹配 / 分类）
├── section_5_inquiry/              询价单（v5 模板 / 调度 / 状态）
├── section_6_masterdata/           主数据（CRUD + 上传管线）
├── section_7_settings_identity/    设置 + 身份认证 + 基础设施
├── section_8_line/                 LINE 集成
│   ├── unit/                       Flex 渲染 / delivery 路由 / session
│   └── integration/                webhook / bind 完整流程
├── section_9_web_api/              所有 FastAPI 路由的 HTTP 契约
└── section_e2e/                    端到端工作流（用 test-orders/ 真 PDF）
```

---

## 9 个 section 的"测什么"

| # | Section | 防的核心 bug |
|---|---|---|
| 1 | 安全核心 | 伪造 webhook、密码明文、token 偷窃、跨用户数据泄露、账号劫持 |
| 2 | 架构守门 | agent 直接写 SQL 绕开 service（K1/K2/K3 bug 的根因） |
| 3 | 文档处理 | 不识别合法文件 / 接受恶意文件 / 加 tag 不去重 |
| 4 | 订单领域 | invoice 被错认成 PO、字段映射丢失、匹配挑错产品、地理错配 |
| 5 | 询价单 | 模板字段填错位置（CLAUDE.md 历史 4 大 bug 出处） |
| 6 | 主数据 | 列表数字错（K1/K2/K3）、commit 不写 changelog、Decimal('0') 当 None |
| 7 | 设置 + 身份 | 暴力破解、改密后 session 不撤销、路径穿越攻击 |
| 8 | LINE | Flex 卡片错位、markdown 表格未识别、webhook 重传 |
| 9 | Web API | RBAC 漏掉、跨用户读、HITL approve 没真 dispatch |
| E2E | 端到端 | 真用户跑完整工作流（上传 → 解析 → 匹配 → 生成询价 → 下载） |

详细 section 内容看 `docs/testing/sections.md`。

---

## E2E 测试用的样本 PDF

真实样本从仓库相邻的 `test-orders/` 目录按文件名加载，包含 7 个采购订单 PDF；公开仓库和 CI 不保存客户文件，缺少整组受控样本时相关测试会明确 skip：

| 文件 | 描述 |
|---|---|
| `68331111-A.pdf` | 中等规模 PO（~60KB）|
| `68358749.pdf` | 中等规模 PO（~80KB）|
| `PurchaseOrder_68007850.pdf` | 小型 PO（~50KB）|
| `PO107309CCI-A.pdf` | CCI-A 系列订单（~160KB）|
| `PO102292CCI-A_compressed (1).pdf` | 大型 CCI 订单（~25MB，压力测试）|
| `Silver Nova - 5111794501-418013.pdf` | Silver Nova 邮轮订单（~150KB）|
| `20251213 PO No CYI-REQ2561 PO01.xlsx - Read-Only.pdf` | Excel 转 PDF 的特殊格式（~70KB）|

E2E 测试会真的把这些文件上传到测试 DB，跑完整管线，验证落库的 Order 数据。

---

## 测试设计原则

1. **每个测试只验证一件事** —— 失败时一眼能看出哪坏了
2. **断言要具体** —— `assert price == 1250.75` 而不是 `assert price is not None`
3. **不 mock 业务行为** —— 只 mock 外部 IO（Gemini / LINE API / Serper）
4. **测试名讲故事** —— `test_employee_cannot_see_other_users_orders` 而不是 `test_orders_1`
5. **fixture 要小** —— 共享的写在 conftest，专用的写在测试文件里
6. **慢测试隔离** —— E2E 单独目录，可以 skip 跑

---

## 维护责任

- 加新功能 → 在对应 section 加测试
- 改 bug → 加回归测试，命名带 "regression" 或 bug 编号
- 新 section？ —— 先更新 `docs/testing/sections.md` 讨论清楚再加目录

---

## 历史背景

- 2026-05-13：基于生产代码**重新写**所有测试（不是迁移）
- 旧 `tests/` 套件：674 个测试，65 个文件，按代码层分目录，混乱 — 已废弃
- 重组 + 重写：按"用户工作流"分 10 个 section（含 E2E），共 896 个测试，59 个文件
- 新增焦距 3 端到端层：用 `test-orders/` 里 7 个真实 PDF 走完整管线（上传 → 解析 → 落库）
- 修复 1 个 race condition：`test_run_inquiry_completes_and_persists_files` 用 `max_workers=1` 替代 `max_workers=2` 以避免 SQLite + thread pool 的连接缓存竞态

## 每段大小（测试数）

| Section | 测试数 |
|---|---:|
| 1 安全核心 | 90 |
| 2 架构守门 | 31 |
| 3 文档处理 | 200 |
| 4 订单领域 | 74 |
| 5 询价单 | 63 |
| 6 主数据 | 71 |
| 7 设置 + 身份 + 基础设施 | 81 |
| 8 LINE（unit + integration） | 125 |
| 9 Web API 集成 | 131 |
| E2E 端到端 | 30 |
| **总计** | **896** |
