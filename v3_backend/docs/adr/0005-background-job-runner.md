# ADR-0005: 后台任务先用进程内 asyncio，Celery 留接口

**日期**: 2026-04-24
**状态**: Accepted
**关联 PLAN**: ADR-5

## 背景

v2 的后台任务（文档抽取、订单匹配、询价生成）做法：

- 直接在 route 里 `loop.run_in_executor(None, run_pipeline, ...)`
- 或者 `threading.Thread(target=..., daemon=True).start()`

问题：
- 无重试、无超时、无监控
- 进程挂掉 → 任务丢失（Cloud Run 冷启动就会丢任务）
- 想做优先级、限流做不到

但引入 Celery / RQ / arq 意味着：
- 额外基础设施（Redis / RabbitMQ）
- 部署复杂度上升
- 开发环境搭建成本

## 决策

**Phase 0-6 用进程内 asyncio，通过 `BackgroundJobRunner` 抽象留好未来接口。**

### 抽象接口

```python
# apps/jobs/runner.py
from typing import Protocol, Awaitable, Callable

class BackgroundJobRunner(Protocol):
    def submit(
        self,
        fn: Callable[..., Awaitable[None]],
        *args,
        job_id: str | None = None,
        **kwargs,
    ) -> str:
        """Schedule a job; return job_id."""

    def cancel(self, job_id: str) -> bool: ...
    def status(self, job_id: str) -> dict: ...
```

### v3 默认实现：AsyncioRunner

- 基于 `asyncio.create_task`
- 维护 `dict[job_id, asyncio.Task]`
- 支持 cancel / status 查询
- 没有持久化（进程重启任务丢失）

### v2 对比的改进

- 不再用 `threading.Thread` + `daemon=True`（不可取消）
- 不再在 route 里散落 `loop.run_in_executor`
- 统一的 job 注册表：`apps/jobs/registry.py`
- 任务函数签名统一（强类型、可测试）

### 迁移到 Celery 的代价（未来）

如果到时决定迁移：
1. 新实现 `CeleryRunner(BackgroundJobRunner)`
2. 在 `main.py` 注入 `CeleryRunner()` 替代 `AsyncioRunner()`
3. 任务函数不改动
4. 预计工作量：1 周

### 不做的事

- **不做**任务持久化（重启会丢，Phase 1-6 可接受）
- **不做**分布式调度
- **不做**优先级队列
- **不做**死信队列

Cloud Run 有最大实例 15 分钟超时 → 超过的任务现在就**不合适**在后台跑，这些任务 Phase 7 之前不会出现。

## 放弃的方案

### 方案 A：直接上 Celery

- ❌ 增加 Redis 依赖 + worker 进程管理
- ❌ 开发环境启动麻烦
- ❌ Phase 1-6 不需要这个复杂度

### 方案 B：用 Supabase Realtime + Postgres 队列

- ❌ 不太成熟的模式
- ❌ 跨项目没有最佳实践

### 方案 C：arq（async Redis queue）

- 技术上合理，但 Redis 依赖仍在
- 留作未来候选

## 影响

- **Phase 0** 建立 `apps/jobs/runner.py` 抽象
- **Phase 2+** 所有后台任务通过 `get_job_runner().submit(...)` 入队
- **Phase 7 后** 评估是否需要 Celery

## 验证

- [ ] 没有任何模块使用 `threading.Thread` 或 `asyncio.ensure_future`（强制走 runner）
- [ ] `BackgroundJobRunner` 有至少 1 个实现（AsyncioRunner）
- [ ] 有 mock runner 用于测试

## 未来升级计划（记录）

当出现以下信号时，立即启动 Celery 迁移：
- 单任务超过 5 分钟且超时频繁
- 需要跨实例共享任务状态
- 需要任务重试 / 死信
- 任务吞吐量超过单进程能力
