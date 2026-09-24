# 商品单位换算生产核验（2026-09-24）

## 结论

商品单位换算第一版已完成生产部署并通过核验。生产数据库为 `0031_unit_conversion_rules`；后端 `cruise-v3-backend-uc-on-20260924` 接收 100% 流量；前端 `dpl_8VjLndA9QmwgeojV7mZ65bfZmSy4` 已绑定正式域名；Oracle Job generation 16 使用同一后端镜像。

当前生产只有 5 条草稿换算规则，0 条已验证规则。默认流程不会自动采用这些候选，PO165047CCI 仍明确展示 53 个 `UNIT_CONVERSION_REQUIRED` 行，等待人工业务确认。

## 源码与 CI

- 功能 PR：#5
- 合并提交：`0dd57b59146bc6f366a10edeebdbb6c454c28c37`
- PR CI：`35989778280`，前后端通过。
- main CI：`35990373774`，前端 47 秒、后端 8 分 12 秒，全部通过。
- 本地门禁：后端 `1811 passed, 94 skipped, 4 warnings`；前端 17 个文件、116 项测试；类型检查、生产构建、架构检查、Ruff 和 wheel 构建通过。

## 数据库

- 发布前按需备份：`1790248065114`，描述 `pre-unit-conversion-0dd57b-20260924`，状态 `SUCCESSFUL`。
- 迁移前现场基线：`0030_direct_bulk_images`、1461 产品、74 订单、32 询价、621 张图片、36 条价格期间，目标规则表不存在。
- 迁移 execution：`cruise-v3-migrate-0031-20260924-txnzt`，成功。
- 迁移后：`0031_unit_conversion_rules`，规则表 22 列、9 个检查约束、3 个索引（含 2 个 partial unique 业务索引）。
- 迁移后核心数量与迁移前现场基线一致；迁移 Job 已锁定为 `RELEASE_APPLY is required`。
- 最终只读核验：`cruise-v3-unit-conv-preflight-20260924-tk4bp`，`verification=PASS`；核验 Job 随后锁定为 `READ_ONLY_RELEASE_CHECK is required`。

## 后端

- Cloud Build：`aa831976-fdfa-4600-b776-136feb2d0916`。
- 镜像：`asia-northeast1-docker.pkg.dev/cruise-v3-prod/cruise-v3-images/backend@sha256:fafc1b01f6b360d136ae3bb3c512a00a3164465168ba8fdad02fd198d75f6100`。
- 先部署 `cruise-v3-backend-uc-off-20260924` 为 0% 流量并保持开关关闭；健康、OpenAPI、GET/POST 未登录 401 和 CORS 通过。
- 再部署 `cruise-v3-backend-uc-on-20260924` 为 0% 流量并开启开关；相同检查及只读业务审计通过后切换 100% 流量。
- 正式 `/health` 返回 revision `cruise-v3-backend-uc-on-20260924`；`UNIT_CONVERSION_RULES_ENABLED=true`。
- 新 revision 唯一 ERROR 级日志为既有 passlib/bcrypt `__about__` 版本读取告警，没有发现本功能运行错误。

## 前端

- 预览部署：`dpl_Gr7p6bCS9Yxb29wb9Z332NtrtLUC`，Ready。
- Production：`dpl_8VjLndA9QmwgeojV7mZ65bfZmSy4`，Ready。
- 正式别名：`https://cruise-v3-frontend.vercel.app`。
- `/dashboard/orders/142` 与 `/login` 均返回 HTTP 200。

## Oracle 自动扫描

- Job：`cruise-v3-po-hourly`，generation 16。
- 镜像 digest 与正式后端完全一致。
- 命令仍为 `python -m scripts.scan_oracle_pos`，原 Oracle 凭证、扫描策略和环境均保留。
- `UNIT_CONVERSION_RULES_ENABLED=true`，避免以后规则通过人工审核后 Web 与定时扫描行为不一致。
- `oracle-po-hourly`、`bulk-image-gc-hourly`、`fx-refresh-daily` 均为 ENABLED。

## PO165047CCI 与规则状态

生产订单 ID 142，共 56 行：53 行已匹配、3 行未匹配。五条草稿是：

1. `KG2.2→KG`，来源单位作用域，影子覆盖 46 行。
2. `CA22.0→CA`，来源单位作用域，影子覆盖 4 行。
3. `CA13.22→CA`，来源单位作用域，影子覆盖 1 行。
4. `CA15.0→CA`，来源单位作用域，影子覆盖 1 行。
5. 商品 1820、包装指纹 `["CT","","86GX12"]` 的 `CA2.27→CT`，影子覆盖 1 行。

默认只读审计结果：`converted=0`、`review=53`、`unmatched=3`。显式包含草稿的影子审计结果：`converted=53`、`review=0`、`unmatched=3`。

数据库最终为 `draft=5`、`verified=0`、`retired=0`。规则 1 的证据计数经实际 rule usage 审计从误写的 48 修正为 46，revision 为 2；没有任何候选被发布脚本自动验证。

## 发布过程中的受控异常

- 第一轮迁移前核验沿用了早前文档中的 1449 产品/73 订单，现场已正常增长为 1461/74，断言因此失败。完整日志证明 head、图片、询价和目标表状态正常；随后以本次迁移前现场值为冻结基线，迁移后逐项一致。
- 第一次草稿创建脚本中的 Unicode 箭头被 ASCII Base64 编码破坏，Python 在解析阶段失败，事务代码没有执行、数据库没有写入。修正为纯 ASCII 的幂等脚本后一次成功创建 5 条草稿。
- Vercel promote 后短时间内正式域名仍指旧版本，是新的 Production deployment 仍在 Building；Ready 后正式 alias 原子切换成功。

## 回滚

首选回滚是同时关闭 Web 服务与 Oracle Job 的 `UNIT_CONVERSION_RULES_ENABLED`，不删除规则表或审计证据。若需代码回滚，可把 Cloud Run 流量切回 `cruise-v3-backend-order-issues-20260924` 并恢复上一前端 deployment；数据库 0031 为 expand-only，应保留，除非在单独批准的停机恢复流程中使用备份 `1790248065114`。
