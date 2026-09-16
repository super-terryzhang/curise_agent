# 系统收敛与重构计划

基线：`main@3f6ffef`  
工作分支：`refactor/system-consolidation-20260916`  
原则：只改善内部结构，不改变可观察行为；不增加功能，不部署生产。

## 执行阶段

1. **Freeze**：以 Git commit、API/页面契约和现有测试固定当前行为，记录明确的环境依赖与跳过项。
2. **Green 1**：运行后端构建、静态检查、完整测试，运行前端单测、类型检查和生产构建，并覆盖关键用户路径。
3. **System review**：按入口、数据流、状态所有权、业务逻辑、错误处理和外部副作用重新阅读系统，而不是只看近期 diff。
4. **Consolidate**：列出重复逻辑、特殊分支、旁路、职责膨胀、边界穿透、临时 helper、死代码、命名和过期文档，并按风险排序。
5. **Characterize**：对缺少保护但准备重构的行为先补特征测试，测试与业务重构分开提交。
6. **Refactor**：每次只处理一个可回滚的问题；每个提交保持构建与相关测试为绿色。
7. **Green 2**：重新执行完整验证矩阵和关键用户路径，比较重构前后的外部行为。
8. **Review**：复核 diff、架构边界、文档和剩余债务，交付用户审查后才进入下一轮 feature。

## 提交边界

- 测试保护、文档修正、代码重构分别提交。
- 不在重构提交中修改 API schema、数据库 schema、用户文案、业务规则或权限规则。
- 若发现真实 bug，先记录并停在诊断结论；修复应使用独立 bug-fix change。
- 单个重构无法小范围完成时，拆分或延后，不制造跨模块大爆炸式修改。

## 完成标准

- 验证矩阵所有本地可执行项通过；外部依赖项有明确原因和独立验收方式。
- 关键路径具有行为级断言，不依赖实现细节或“change detector”测试。
- 系统地图和问题清单能说明每项状态与逻辑的唯一责任方。
- 重构前后公开 API、文件输出、权限和用户流程保持一致。

## 方法依据

- Martin Fowler：Refactoring 是在不改变可观察行为的前提下改善内部结构：<https://refactoring.com/>
- Google Engineering Practices：重构与功能/修复应拆成小而自洽的 change：<https://google.github.io/eng-practices/review/developer/small-cls.html>
- Google Engineering Practices：review 需要把改动放回系统上下文检查复杂度、测试和文档：<https://google.github.io/eng-practices/review/reviewer/looking-for.html>
