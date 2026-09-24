# 单位换算规则激活与历史订单刷新核验（2026-09-24）

核验时间：2026-09-24 22:25–22:49 JST。

## 最终结果

用户明确确认现有 5 条候选关系按 1:1 生效并授权刷新全部订单后，生产数据库中的 5 条规则已由 `draft` 审核为 `verified`。数据库保持 `0031_unit_conversion_rules`，生产后端仍为 `cruise-v3-backend-uc-on-20260924`、100% 流量且 `UNIT_CONVERSION_RULES_ENABLED=true`；本轮没有构建或部署新应用代码。

正式执行 `cruise-v3-unit-conv-preflight-20260924-rs9bj` 在一个数据库事务内完成规则审核、75 个订单的单位换算派生字段刷新和异常检测重算。结果为：

- 665 行采用已验证规则完成换算，仍有 461 行单位关系需要人工确认；
- 历史商品匹配决定变化为 0，未重新选择数据库商品，也没有改写订单港口或原始 PO 商品行；
- 425 行仍为商品未匹配，单位规则不会掩盖未匹配问题；
- PO165047CCI（订单 142）共 56 行：53 行已换算、0 行单位待确认、3 行商品未匹配。

独立只读审计 `cruise-v3-unit-conv-preflight-20260924-gxzlj` 再次确认 PO165047CCI 为 `matched=53`、`converted=53`、`review=0`、`unmatched=3`，未匹配原因计数为 `PRODUCT_NOT_MATCHED=3`。

## 页面曾显示 56 的原因与后续修复

订单当前匹配状态更新后，供船安排 40 的最新正式询价仍是历史 version 1（inquiry 38，状态 `unmatched`），它保留了规则激活前的 57 条排除记录。旧代码把这些历史询价结果纳入当前 PO 问题统计，因此 PO165047CCI 曾继续显示 56 行待处理；这不代表 5 条规则没有生效。

安排级回滚预演 `cruise-v3-unit-conv-preflight-20260924-kq22f` 证明生成 version 2 可以刷新历史询价，但会创建新的正式供应商文件，因此不是修复列表统计的正确前置条件。后端随后通过 PR #8 将历史 `RFQ_ROW_EXCLUDED` 与当前 PO 问题分离；PO165047CCI 现显示 3 项、PO167144CCI 显示 1 项，完整证据见 [当前 PO 待处理统计修复核验](DEPLOYMENT_VERIFIED_2026-09-24_CURRENT_PO_ISSUES.md)。

## 为什么没有直接运行完整匹配器

第一次受保护执行 `cruise-v3-unit-conv-preflight-20260924-nqm6c` 只解析出 55 行后触发断言并整笔回滚。证据定位发现部分历史 `match_results` 快照的 `unit` 已被旧流程保存成供应商单位，真实订购单位仍完整保存在 `order.products`。

随后完整匹配预演 `cruise-v3-unit-conv-preflight-20260924-9lt58` 虽然最终回滚，但显示它会改变 49 个订单的商品匹配结论、12 个订单的地域信息，并增加未匹配行，因此被判定超出“只激活单位规则”的安全边界。最终方案从原始 PO 行读取来源数量和单位，只刷新 `source_quantity/source_unit/rfq_quantity/rfq_unit/conversion_evidence/unit_conversion_issue` 及异常结果，保留既有匹配身份和基础快照。

安全方案预演 `cruise-v3-unit-conv-preflight-20260924-xndbt` 成功并回滚：覆盖 665 行，商品匹配决定 0 处变化，PO165047CCI 达到 53/0/3 的预期后才执行正式事务。

## 安全与回退

- 正式执行前创建 Cloud SQL 按需备份 `1790256314877`（`pre-unit-rules-verify-20260924`），状态 `SUCCESSFUL`。
- 正式脚本固定校验生产环境、数据库 head、功能开关、管理员身份、5 条规则的 ID/状态/版本/作用域/数量关系，以及 665/461/425 和 PO165047CCI 的结果；任一不符即回滚。
- 规则最终 revision 为：规则 1 为 3，规则 2–5 为 2；审核人均为系统管理员账号 1。
- 运维 Job 已移除正式批准令牌并重新锁定，避免预演或正式脚本被误重复执行。
- 如需业务回退，优先在管理页逐条停用规则并重新生成派生结果；灾难恢复点为上述备份。不要直接删除 0031 表或审计记录。

日志中的 passlib/bcrypt `__about__` 堆栈是既有依赖告警，本次 Job 均按预期结束，不影响换算事务与只读核验。
