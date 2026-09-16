# 系统收敛进度

更新时间：2026-09-16

## 当前阶段

- [x] 冻结基线：`main@3f6ffef`
- [x] 创建独立重构分支
- [x] 完成第一轮完整验证
- [x] 完成系统数据流与职责地图
- [x] 完成 debug 伤口清单与风险排序
- [x] 补齐重构前行为测试
- [x] 分批完成安全重构
- [x] 完成第二轮完整验证
- [x] 完成最终 diff、安全与边界 review
- [ ] 用户 review 后决定是否合并；本轮不部署生产

## 已知边界

- 本轮不增加功能、不改变业务规则、不部署生产。
- 真实 LLM、PostgreSQL、Oracle、LINE、GCS 和真实 PO 样本测试需要单独受控环境；不能用本地跳过冒充已验证。
- 仓库为 Public，真实业务文件、凭证和生产证据不进入 Git。

## 执行日志

- 建立 `refactor/system-consolidation-20260916`，以首次公开 v3 基线作为行为冻结点。
- 初读发现后端 README 引用已不存在的文档和旧测试目录，已登记为文档债务，尚未修改。
- 后端完整行为回归：`1718 passed, 79 skipped`；跳过项为受控外部环境测试。
- 前端：`89 passed`，TypeScript 通过，沙箱外生产构建通过并生成 15 个路由。
- 后端架构检查：0 违规；Alembic 单 head 为 `0029_arrangement_inquiries`。
- 后端 wheel 和 Docker 构建均已启动验证；wheel 内容检查发现子包未正确打入，记为 BUILD-001。
- 全仓 Ruff 基线 287 项、格式基线 245 文件、strict mypy 基线 496 项/76 文件；作为既有质量债务分批处理，不进行大规模自动改写。
- 对自动归组异常路径先补失败回归，再以独立 bugfix `347921f` 修复：订单保留 `ready`，进入未分类和人工处理，不再被二次异常改成 `error`。
- 独立重构 `45b216d` 把通用后台任务 runner 从 `apps/` 下沉到 `infrastructure/`，并新增 domain → apps 禁止规则；相关 226 项回归与 28 项架构测试通过。
- 独立构建修复 `beea5d5` 使用包自动发现并打入 `main`、询价模板和 Agent skills；wheel 在隔离目录安装后完成导入和资产发现烟雾测试。
- 文档提交 `5548834` 移除失效 Phase 0 指引；CI 提交 `3a78418` 加入后端行为/架构/wheel 与前端测试/类型/构建门禁。
- 第二轮后端完整回归：`1721 passed, 79 skipped, 4 warnings`；跳过项仍仅为受控外部环境矩阵。
- 第二轮前端：`89 passed`，TypeScript 通过，生产构建通过并生成 15 个路由。
- 最终 Docker 镜像 `curise-backend:consolidation-final` 构建成功；容器内应用导入、标准询价模板和 `/health` 均通过。
- 当前 review diff 为 33 个文件、442 行增加、145 行删除（包含 4 份收敛文档）；未包含数据库迁移、生产配置、凭证或真实业务文件。
- 首次 GitHub runner 发现测试依赖本机 `.env`、真实 PDF 和绝对路径；`f6158ad` 将这些依赖改为显式假 key、仓库相对路径及受控 fixture 缺失时 skip。
- 在无 `.env`、无 `test-orders` 的干净 worktree 重跑首次 CI 的全部失败集合：`40 passed, 14 skipped`；跳过原因逐项显示为受控真实样本缺失。
- 本轮仍未部署生产；用户 review 和合并之前，不进入下一轮 feature。
