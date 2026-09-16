# ADR-0006: 目录边界由机器强制执行

**日期**: 2026-04-24
**状态**: Accepted
**关联 PLAN**: ADR-6

## 背景

v2 的代码边界靠"约定"维护，结果：
- `services/documents/document_processor.py` 反向 import `services.orders.order_processor.smart_extract`
- `services/orders/inquiry_agent.py` 直接 import `services.agent.stream_queue`
- `services/tools/*.py` 随意 `db.query(Product)` 跨 domain

10 人团队，没人专职把关，两年后边界就被磨平。靠 review 拦不住——review 者不会记住所有规则。

## 决策

**边界由 CI 自动执行，违反就阻断 PR 合并。**

### 六条硬规则（全部机器可查）

1. **业务 → Agent 禁止**
   - 正则: `^from agent(\.|\s)` 出现在 `domains/**/*.py` 或 `shared/**/*.py` → 失败

2. **Agent → DB 直写禁止**
   - 正则: `\bdb\.(commit|add|query|delete|flush)\b` 出现在 `agent/runtime/tools/**/*.py` → 失败
   - 白名单: `agent/storage/*.py`、`agent/memory/*.py`、`agent/runtime/session_store.py`、`agent/runtime/memory_adapter.py`（持久化适配层，算基础设施）
   - 历史注脚：ADR-0007 之前规则覆盖 `agent/tools/**`、`agent/engine/**`；这些目录已删，规则同步搬到新路径。

3. **跨域只能通过 service 接口**
   - 正则: `^from domains\.<X>\.(?!service|schemas|__init__)` 出现在 `domains/<Y>/` 且 X ≠ Y → 失败
   - 允许: `from domains.<X>.service`、`from domains.<X>.schemas`、`from domains.<X> import <public-name>`
   - 禁止: `from domains.<X>.models`、`from domains.<X>.repository`（跨域访问他人的 ORM 或 SQL 是最大的耦合源）

4. **shared/ 不得依赖业务**
   - `from domains.*`、`from agent.*`、`from apps.*`、`from infrastructure.*` 出现在 `shared/**/*.py` → 失败
   - shared 只放纯函数（无 I/O、无状态、无业务概念）

5. **LINE 入口不得依赖 HTTP 入口**
   - `from apps.http.*` 出现在 `apps/line/**/*.py` → 失败
   - 两者是并列应用入口；共用逻辑必须下沉到 domain、agent 或 infrastructure

6. **Domain 不得依赖应用入口**
   - `from apps.*` 或 `import apps.*` 出现在 `domains/**/*.py` → 失败
   - 定时任务、HTTP 和 LINE 可以调用 domain；domain 不能反向调用入口层

### 落地：`scripts/check_arch.py`

- 独立 Python 脚本，无依赖
- 遍历所有 `.py` 文件，正则匹配
- 输出违规清单
- 非零退出码 → CI 失败

### CI 集成

```yaml
# .github/workflows/ci.yml
- name: arch-check
  run: python scripts/check_arch.py
```

### 本地开发：pre-commit hook

```yaml
# .pre-commit-config.yaml
- repo: local
  hooks:
    - id: arch-check
      name: module boundary check
      entry: python scripts/check_arch.py
      language: system
      pass_filenames: false
```

## 放弃的方案

### 方案 A：Code Review 靠人记住

- ❌ 人会忘
- ❌ 新人不知道
- ❌ 规则增多后 review 成本指数级上涨

### 方案 B：`__init__.py` 的 `__all__` 控制

- ❌ Python `__all__` 只影响 `from x import *`，挡不住 `from x.module import something`
- ❌ 静态检查工具对 `__all__` 的支持不完整

### 方案 C：`import-linter` 或 `pydeps`

- 工具本身可行，但依赖 TOML 配置
- 自己的 `check_arch.py` 只 50 行且无外部依赖，更简单

## 影响

- **Phase 0 必做**：写 `scripts/check_arch.py` + 配 CI
- **每次 refactor** 自动检测新违规
- **新成员**写代码时，CI 红灯会立刻告诉他"你越界了"，不需要 review 者解释

## 验证

- [x] `scripts/check_arch.py` 存在，跑 `python scripts/check_arch.py` 返回 0
- [ ] CI pipeline 包含 arch-check job
- [x] 规则单元测试会故意制造违规并验证能够检测
- [x] 预提交 hook 已配置 arch-check

## 演进

未来可以扩展：
- 检测循环依赖（`pydeps --show-cycles`）
- 强制每个 domain 有 `service.py`
- 强制 service 函数签名不包含 ORM 对象（只用 schemas）
