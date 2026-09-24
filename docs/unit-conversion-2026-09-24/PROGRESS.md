# 商品单位换算系统进度

日期：2026-09-24

## 2026-09-24 23:50：当前 PO 待处理统计已修复并部署

`/dashboard/orders` 不再把历史询价版本的 `RFQ_ROW_EXCLUDED` 当成当前 PO 未匹配。无需生成新询价，PO165047CCI 现在按当前数据库结果显示需处理 3 项，PO167144CCI 显示需处理 1 项；旧询价版本与审计记录仍保留。

修复已通过 PR #8 合并为 `00b4d46be672fad3cb5b6be788e741b9e0e7b2f8`，GitHub CI `36013352290` 前后端成功。正式后端为 `cruise-v3-backend-po-current-issues-20260924`、100% 流量，镜像 digest `sha256:b16675fb1b43cca5781c883d7226ca9d4f57e73c320514e987c99a359bfe9d51`；数据库保持 0031，详情见根目录 [当前 PO 待处理统计修复核验](../../DEPLOYMENT_VERIFIED_2026-09-24_CURRENT_PO_ISSUES.md)。

Oracle Job generation 17 已于 2026-09-25 00:00 JST 完成首次自动执行 `cruise-v3-po-hourly-qbj2l`；任务成功，业务 run 366 与上一轮的 32 项结果完全一致，没有新导入结果或新询价。

## 2026-09-24 22:49：五条规则已激活，历史订单已安全刷新

用户确认现有五条候选关系按 1:1 生效后，生产规则已全部由草稿审核为已验证；75 个订单的单位换算派生字段和异常结果已刷新，但历史商品匹配决定 0 处变化。共 665 行完成规则换算，仍有 461 行单位关系待人工确认、425 行商品未匹配；PO165047CCI 当前为 53 行已换算、0 行单位待确认、3 行商品未匹配。

本轮没有部署新应用代码，数据库保持 0031，后端仍为 `cruise-v3-backend-uc-on-20260924` 且开关已开启。正式执行前备份 `1790256314877` 成功，写入 execution 为 `cruise-v3-unit-conv-preflight-20260924-rs9bj`，独立只读复核为 `cruise-v3-unit-conv-preflight-20260924-gxzlj`；完整证据见根目录 [单位换算规则激活核验](../../DEPLOYMENT_VERIFIED_2026-09-24_UNIT_CONVERSION_ACTIVATION.md)。

此前页面的 56 行待处理来自供船安排 40 的历史询价 version 1，并非当前单位换算结果。现已从“当前 PO 问题”统计中分离，不需要为了刷新列表而生成 version 2；只有用户确实需要新的正式供应商文件时才生成新询价版本。

## 当前结论

商品单位换算第一版已经合并并部署生产。功能 PR #5 合并提交为 `0dd57b59146bc6f366a10edeebdbb6c454c28c37`；生产数据库已从 `0030_direct_bulk_images` 升到 `0031_unit_conversion_rules`，后端 `cruise-v3-backend-uc-on-20260924` 接收 100% 流量，正式前端为 `dpl_8VjLndA9QmwgeojV7mZ65bfZmSy4`。

系统不会从 `KG2.2`、`CA2.27` 的数字后缀自行猜测业务含义。发布时创建的 5 条规则最初全部保持草稿；用户随后明确确认 1:1 关系并授权全量刷新，因此它们现已审核为 `verified`。

## 已完成

- 新增 expand-only 迁移 `0031_unit_conversion_rules`，保存来源单位规则、商品包装规则、有效期间、换算基数、订购步长、拆包三态、证据、审核人、版本和时间戳；迁移没有回填业务事实。
- 新增 Decimal 换算服务、严格作用域选择、规则审核/停用接口、订单行人工处理和逐行异常隔离。
- 订单页可区分“未匹配”和“已匹配但单位有问题”，普通人员只能处理当前行；可复用商品或来源单位规则需要管理员权限。
- 匹配流程只采用已验证且作用域完全一致的规则；询价准备只信任完整的已验证换算快照。
- 前后端保留精确十进制文本，不经过 JavaScript Number 或 Python float 作为业务换算边界。
- 新增生产只读影子审计脚本，可分别评估正式规则和草稿覆盖率，不修改订单或规则。
- Web 后端与 Oracle Job 均使用 `UNIT_CONVERSION_RULES_ENABLED=true`；当前没有已验证规则，因此开关开启不会自动套用五条草稿。

## PO165047CCI 生产验收

生产订单 ID 142，共 56 行：53 行已匹配、3 行未匹配。影子审计确认五条候选实际覆盖为：46 行 `KG2.2→KG`、4 行 `CA22.0→CA`、各 1 行 `CA13.22→CA`、`CA15.0→CA` 和商品 1820 的 `CA2.27→CT`。

- 默认审计（不含草稿）：`converted=0`、`review=53`、`unmatched=3`。
- 影子审计（显式含草稿）：`converted=53`、`review=0`、`unmatched=3`。
- 数据库最终为 5 条 `draft`、0 条 `verified`、0 条 `retired`；特别是 `CA2.27→CT` 没有被自动认定为业务事实。
- 规则 1 的证据计数在影子审计后从误写的 48 修正为 46，revision 从 1 增加到 2；规则逻辑与状态未改变。

## 验证与发布证据

- 本地后端：`1811 passed, 94 skipped, 4 warnings`；架构检查 0 违规，Ruff、wheel 构建通过。
- 本地前端：17 个测试文件、116 项测试通过；类型检查与 Next.js 生产构建通过。
- PostgreSQL 演练：`0030→0031→0030→0031` 成功，产品、订单、询价和价格哨兵哈希不变。
- PR CI：`35989778280` 通过；合并后 main CI：`35990373774` 通过。
- Cloud SQL 按需备份 `1790248065114` 为 `SUCCESSFUL`。
- Cloud Build `aa831976-fdfa-4600-b776-136feb2d0916` 成功；镜像 digest 为 `sha256:fafc1b01f6b360d136ae3bb3c512a00a3164465168ba8fdad02fd198d75f6100`。
- 迁移 execution `cruise-v3-migrate-0031-20260924-txnzt` 成功，随后迁移 Job 已锁定。
- 迁移后结构为 22 列、9 个检查约束、3 个索引，其中 2 个为 partial unique 业务索引。
- 后端 0% 候选分别验证开关关闭与开启；健康、OpenAPI、未登录 401 和正式前端 CORS 均通过，再切 100% 流量。
- Vercel Production `dpl_8VjLndA9QmwgeojV7mZ65bfZmSy4` 为 Ready，正式 `/dashboard/orders/142` 与 `/login` 返回 200。
- Oracle Job generation 16 与后端使用同一镜像 digest，单位换算开关开启；三个 Scheduler 均为 ENABLED。
- 最终只读核验 execution `cruise-v3-unit-conv-preflight-20260924-tk4bp` 通过：1461 产品、74 订单、32 询价、621 张图片、36 条价格期间，核心数量与迁移前现场基线一致。

完整发布证据见根目录 `DEPLOYMENT_VERIFIED_2026-09-24_UNIT_CONVERSION.md`。

## 已知问题与待办

- 请用户在正式页面验收 PO165047CCI 已不再显示 53 条单位待确认，并检查仍保留的 3 条未匹配商品。
- 新出现且没有已验证规则的单位组合仍会进入人工确认；系统不会根据单位文字中的数字自行猜测关系。
- passlib/bcrypt 的版本读取堆栈仍会出现在新 revision 日志中，是既有依赖债务；本轮健康、认证和业务核验没有受影响。
- 本次发布前只读核验最初把 2026-09-24 早前的 1449/73 当成固定数量而失败；现场证据确认生产已正常增长为 1461/74 后，核验改为冻结本次迁移前现场值并在迁移后对比。
- 一次性草稿创建 Job 曾因 Unicode 箭头被错误的 ASCII Base64 编码而在 Python 解析阶段失败；代码未执行、数据库未写入，改为纯 ASCII 后成功。

## 回滚原则

功能回退优先把 Web 与 Oracle Job 的 `UNIT_CONVERSION_RULES_ENABLED` 同时设为 false，系统即恢复既有单行人工处理。代码回退时保留 0031 表和草稿审计记录；生产有流量时不盲目 downgrade，数据库灾难恢复只使用发布前按需备份并单独确认。
