# 收敛问题清单

这里只记录有源码、测试或运行证据的问题。重构候选必须先说明外部行为保护方式，再进入代码修改。

| ID | 范围 | 现象与证据 | 风险 | 处理状态 |
|---|---|---|---|---|
| DOC-001 | 后端 README | 引用仓库中不存在的 `PLAN.md`、`AGENT_JOURNAL.md`，测试命令仍指向已删除的 `tests/` | 新开发者会按错误路径操作，无法复现当前验证流程 | 已修复：`5548834` |
| BUG-001 | 自动归组失败路径 | `auto_group_order()` 捕获异常后返回 `None`，`automatic_order_pipeline()` 随后调用 `classification.get()` | 本应保留订单并进入人工队列的归组失败会升级为整个自动流程异常 | 已补 E2E 并修复：`347921f` |
| ARCH-001 | 后台任务边界 | `domains/document/service.py` 直接导入 `apps.jobs.runner`，现有架构门禁没有禁止 domain → apps | 领域层依赖入口层，任务执行机制难以替换，系统地图与实际依赖方向不一致 | 已重构并新增规则 6：`45b216d` |
| BUILD-001 | Python wheel | wheel 构建成功但只包含顶层包和 `general_agent`，大部分 `apps/domains` 子包没有打包 | Docker editable install 掩盖配置错误，独立 wheel 无法运行 | 已修复并隔离安装验证：`beea5d5` |
| QUAL-001 | Ruff | `ruff check .` 基线 287 项；`ruff format --check .` 会改 245 个文件 | 当前配置声称的全仓质量门禁实际不可用；一次性格式化会形成不可审查的大 change | 分批收敛 |
| QUAL-002 | mypy | strict mypy 基线 496 项/76 文件 | 类型门禁实际不可用，空值、返回类型和第三方接口问题无法增量阻断 | 分模块收敛 |
| MOD-001 | 产品上传 | `domains/masterdata/upload/service.py` 2227 行，混合解析、匹配、差异、提交、回滚和工作台 DTO/中文化 | 修改任一阶段都需要理解整个文件，重复映射和状态判断容易漂移 | 先补边界测试，再分阶段拆分 |
| MOD-002 | 询价 | `orchestrator.py` 825 行，同时维护单 PO 兼容流和安排级版本流；HTTP inquiry 还包含 HTML preview | 新旧路径容易产生状态和错误处理分叉 | 暂不大拆，先稳定契约 |
| UI-001 | 前端大型模块 | 文档详情 1951 行、SupplierTemplateTab 1671 行、orders-api 1291 行，PO 与安排页重复 Panel/StatusText | 请求、转换和展示耦合，review 难以看清局部行为 | 可从无状态展示组件开始小步提取 |
| STATE-001 | 状态值 | 多个 domain 和前端以字符串重复判断 status，缺少集中枚举/转换边界 | 新增状态时容易漏掉分支；当前不能一次性替换以免改契约 | 建议按单 domain 收敛 |
| ASYNC-001 | 后台执行 | runner 的进程内任务表不跨实例；无 event loop 时启线程但没有登记线程状态 | 状态查询/取消不可靠，进程重启或多实例会丢失运行态 | 属于后续架构 change，不在纯重构中偷改 |
| STUB-001 | 公开 API/注释 | 仍有 set-template、交付环境、AI skills 等 501 入口及大量旧 Phase 注释 | 容易误判“已实现/未实现”的真实边界，可能让前端调用死接口 | 先确认调用方，再独立删除或实现 |
| TEST-001 | 外部验收 | 完整本地回归跳过 79 项，原因是 LLM、PostgreSQL、真实样本等受控依赖 | 不能把本地绿色等同于所有真实集成已验证 | 发布前单独执行外部矩阵 |
| TEST-002 | 测试隔离 | 首次 GitHub CI 暴露真实 PDF、本机绝对路径和本地 `.env` API key 依赖 | 本机绿色无法在公开仓库的干净 runner 复现，会让 CI 失去保护价值 | 已修复并用无 `.env`/无客户文件 worktree 验证：`f6158ad` |
| CI-001 | GitHub | 新公开仓库尚无 CI workflow | 当前测试保护依赖人工执行，后续 change 可能绕过基线 | 已建立行为门禁：`3a78418` |
| DEP-001 | Python 依赖 | Passlib 初始化时读取 bcrypt 已移除的 `__about__.__version__`，当前会捕获异常并继续 | 目前认证测试绿色，但升级 Python/依赖时可能变成兼容性故障 | 独立升级与认证回归，不混入本轮重构 |
| DEPR-001 | 测试依赖 | FastAPI TestClient 的 httpx 兼容层及旧 413 常量产生 4 条弃用警告 | 现在不影响行为，但未来升级会放大为破坏性变更 | 分别跟随 Starlette/FastAPI 升级处理 |
