# PO 商品供应商名称生产核验（2026-09-26）

核验时间：2026-09-26 16:43–17:16 JST。

## 结论

PO 详情“商品明细”的供应商列已改为显示当前商品匹配对应的供应商主数据名称，不再依赖旧询价快照，也不再显示不透明的“供应商 #2 / #3”。主数据确实缺失时会明确显示“供应商资料缺失（ID …）”，避免把数据问题伪装成正常名称。

本轮没有数据库迁移、没有重新匹配订单、没有修改异常统计、单位换算或询价版本。生产数据库保持 `0031_unit_conversion_rules`；PO165047CCI 仍为 56 行、53 行当前匹配、3 项需处理。

## 根因与修复边界

PO 详情表格使用当前 `order.match_results`，但供应商名称映射来自最近一次询价快照。当前匹配更新后，如果旧询价没有包含相应供应商，前端只能回退为“供应商 #ID”，因此匹配事实正确但名称展示错误。

修复后，后端从当前匹配结果收集供应商 ID，并通过一次批量查询把供应商主数据名称加入问题总览；前端优先使用这一当前名称。旧接口的询价供应商映射只作为滚动发布兼容回退，真实主数据缺失则显示明确的数据缺失提示。

## 源码与验证

- 功能提交：`c1333cb0483b3f8f612562304a41bc1b29e3584b`。
- GitHub PR：[#10](https://github.com/super-terryzhang/curise_agent/pull/10)。
- 合并提交 / 生产源码：`531db856de74bb6b5c01e6b266aaccee208ba695`。
- PR CI：`36228224683`，frontend 52 秒、backend 8 分 5 秒，均成功。
- main CI：`36228653584`，frontend 46 秒、backend 7 分 57 秒，均成功。
- 本地后端：`1812 passed, 94 skipped, 4 warnings`；相关接口回归 33 项通过，架构检查、Ruff、wheel 构建/安装/资源导入通过。
- 本地前端：21 个测试文件、`126 passed`；TypeScript 与 Next.js 正式构建通过。
- 构建来源：合并提交的精确 Git 归档，归档 SHA-256 为 `70e8c0a886432cf758eaf6c98910187d3b3a53cf183bac61359a3ca28d6c2bf7`。

## 后端与生产数据

- Cloud Build：`001b197e-7f26-4d90-87b2-4f9c73d56317`，状态 SUCCESS。
- 镜像：`asia-northeast1-docker.pkg.dev/cruise-v3-prod/cruise-v3-images/backend@sha256:ed36b3e8cd1fcdff1ec6da9728553864b11114db985e8a52ab27ff4e8833daff`。
- 候选 revision：`cruise-v3-backend-supplier-names-20260926`，先以 0% 流量启动；候选 `/health=200`、未登录 PO API `401`、正式前端 Origin 的 CORS 预检 `200`。
- 候选只读生产核验 execution：`cruise-v3-unit-conv-preflight-20260924-2kpjh`，成功且事务最终 rollback；核验 Job 默认命令仍锁定为 `READ_ONLY_RELEASE_CHECK is required`。
- 正式 revision 现接收 100% 流量，正式 `/health=200` 并返回正确 revision；`/api/orders/142` 未登录仍为 `401`。

只读生产断言结果：

| 项目 | 结果 |
|---|---|
| 数据库 head | `0031_unit_conversion_rules` |
| PO | `PO165047CCI`（order 142） |
| 商品行 / 当前匹配 | 56 / 53 |
| 当前需处理 | 3 |
| 当前供应商名称 | `㈲田浦中央食品`、`株式会社　松武`、`株式会社三祐` |

## 前端、Job 与调度

- Vercel 预览候选：`dpl_HSM58oiyTmJ28zpSnLoY5DFqYqrE`，Ready。
- Vercel Production：`dpl_BVFDhf8WnSJ9TfbrpVnG3ioA1Eq1`，Ready，正式域名 `https://cruise-v3-frontend.vercel.app` 已指向该部署。
- 正式 `/login` 与 `/dashboard/orders/142` 均返回 HTTP 200；自动化没有使用生产账号修改订单或生成询价。
- Oracle Job `cruise-v3-po-hourly` 已更新为 generation 18，使用与正式后端相同的不可变 digest，命令仍为 `python -m scripts.scan_oracle_pos`。
- `oracle-po-hourly`、`bulk-image-gc-hourly`、`fx-refresh-daily` 均保持 ENABLED。generation 18 的首次自然调度为 2026-09-26 18:00 JST，属于上线后观察项，发布期间没有手动触发扫描。

## 已知债务与回退

- 候选和只读 Job 唯一 ERROR 级应用日志仍是既有 passlib/bcrypt `__about__` 兼容告警；健康检查、接口保护和只读业务断言均成功，本轮没有扩大处理范围。
- 后端可把 100% 流量切回 `cruise-v3-backend-po-current-issues-20260924`，并把 Oracle Job 镜像同步恢复为 `sha256:b16675fb1b43cca5781c883d7226ca9d4f57e73c320514e987c99a359bfe9d51`。
- 前端可恢复上一 Production `dpl_D1ArQ9TcAjCyXQi38Apy38hEScJr`。本轮无数据库变更，不需要数据库回退。
