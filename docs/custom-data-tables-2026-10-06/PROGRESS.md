# 自定义数据表实施进度

## 状态

用户已批准原生执行；独立分支 `feature/custom-data-tables-20261006`，起点 `3c9c69a`。尚未合并、推送、迁移生产或部署。

## 执行清单

1. 存储、请求契约和新迁移：完成本地 ORM/契约验收；真实迁移留 Task 7。
2. 八类型校验：开始执行。
3. 表和字段管理：待执行。
4. 记录、关联、唯一值和历史原子保存：待执行。
5. 归档恢复及查询：待执行。
6. 受保护接口：待执行。
7. 真实 PostgreSQL 约束、并发、迁移：待执行。
8. 前端接口及状态：待执行。
9. 设置入口及结构页面：待执行。
10. 记录、关联、历史页面：待执行。
12. 本地发布兼容开关：完整验证前执行。
11. 完整用户路径、技术债务复核及稳定候选一次全量：待执行。
12. 生产合并、推送、部署：待阶段授权。

## 验证原则

每任务先失败反例，再实现和针对性测试；完整回归只在稳定候选运行一次。业务数据仅本地合成，现有产品、价格、订单结构不改。默认无真实 LLM 或生产数据库访问。

## Task 1 证据

- 先运行新 test_models：6 failed，原因全部为目标新模块尚不存在，非语法/依赖问题。
- 隔离工作树导入路径实际指向本工作树；复用主目录解释器，未安装或升级 Python 依赖。
- 实现六模型及六表增量迁移：表、字段、记录、关联、唯一占位、只追加历史；复合外键阻止错来源表、错字段、错目标表和错目标记录，既有 Product 列不变。
- 新模型专项 + 既有 release_maintenance：13 passed；Ruff 和架构检查通过；本地 Alembic 仅 `0035_custom_data_tables (head)`。
- 命令从工作树 v3_backend 执行：主目录 `.venv/bin/python -m pytest test_v2/integration/dynamic_data/test_models.py test_v2/section_1_security/test_release_maintenance.py -q`；解释器绝对路径 `/Users/yichuanzhang/Desktop/curise_system_2/curise_agent/v3_backend/.venv/bin/python`。
- ORM create_all 不是生产迁移证明；尚未连接任何 PostgreSQL 或生产库。既有 Starlette/passlib 弃用告警不在本轮扩展修复。
