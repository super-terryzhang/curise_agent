# Cruise Backend v3

邮轮采购系统后端，基于 FastAPI、SQLAlchemy 和 Alembic。当前实现覆盖 PO
导入与自动处理、供船安排、产品与价格期间、批量上传、询价单生成、AI 工作台及
LINE 入口；实际生产基线与待办以仓库根目录的 `PROGRESS.md` 和部署核验记录为准。

## 本地启动

要求 Python 3.11。环境变量从 `.env.example` 开始配置；不要提交 `.env`、真实密钥、
客户文件或生产数据库。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn main:app --reload --port 8000
```

本地健康检查为 `http://localhost:8000/health`。Swagger 只在 `DEBUG=true` 时开放于
`http://localhost:8000/docs`；生产迁移必须作为独立发布步骤执行，不能由自动扩容实例并发执行。

## 验证

```bash
# 当前本地可重复的完整行为回归（外部环境测试会明确 skip）
pytest

# 架构边界
python scripts/check_arch.py

# 构建 Python wheel；Docker 是正式服务部署产物
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir /tmp/cruise-wheel

# 容器构建
docker build -t cruise-backend-v3 .
```

真实 LLM、一次性 PostgreSQL、Oracle、LINE、GCS 和真实 PO 样本属于受控外部验收，
不能用默认跳过结果替代。全仓 Ruff 与 strict mypy 仍有已登记的历史基线债务；在完成
分批收敛前，应对改动文件增量检查，不能声称这两项已经全仓绿色。

## 代码结构

- `apps/`：HTTP、LINE、定时任务等应用入口与装配。
- `domains/`：文档、订单、询价、主数据、身份和设置等业务规则。
- `infrastructure/`：数据库、文件存储、Oracle 和后台任务执行适配器。
- `agent/`、`general_agent/`：AI 工作台运行时、工具与审批。
- `test_v2/`：当前唯一有效的 pytest 测试树。
- `docs/adr/`：架构决策；模块边界由 `scripts/check_arch.py` 强制执行。

关键边界包括：Agent 只能通过领域服务访问业务；跨领域访问只能经过公开服务契约；
领域层不得反向依赖 `apps/`；后台任务抽象位于 `infrastructure/jobs/runner.py`。

## 当前收敛工作

本轮系统复核、验证矩阵和技术债务证据记录在
`../docs/consolidation/2026-09-16/`。该工作只重构内部结构，不改变 API、数据库 schema、
权限、业务规则或用户流程，也不代表已部署生产。
