# 2026-10-01 页面内已有产品批量编辑生产核验

核验时间：2026-10-01 23:02–23:31 JST。结论：新四阶段已有产品更新已合并到 main、推送 GitHub，前后端正式发布完成；重新登录后正式账号选品、跨页全选和编辑页面核验通过。数据库未迁移，没有提交生产产品、价格或上传批次写入；正式写入及回滚仍由用户检查。

## 发布结果

| 项目 | 最终值 |
|---|---|
| 生产功能源码 | `eacc003b2e977053f1bd2f19291891193ee783d4`，合并树与开发提交 `74cf46c` 完全一致 |
| GitHub CI | `36873497986`，frontend/backend 均 success |
| Cloud Build | `3a794adf-3f59-4658-8980-bf777398bcf5`，SUCCESS |
| 后端 | `cruise-v3-backend-direct-edit-20261001`，100% 流量 |
| 镜像 digest | `sha256:eaeffbf2074000d5d9ea89fad98be31e8bf32b295bfa89ba6f946f2b60abc6b6` |
| 前端 | `dpl_7NcgHVr9aeGn3JAGyeFVCDsSff3F`，Production / Ready，已 promote |
| 正式入口 | https://cruise-v3-frontend.vercel.app/dashboard/workbench/product-update |
| 数据库 | `0034_drop_product_validity`，未迁移 |
| Oracle Job | generation 25，Ready，与后端同一不可变镜像 |
| Scheduler | 三个均 ENABLED，未额外手动扫描 |

## 上线行为

1. 选择产品：按代码/名称、类别、供应商、国家、港口、状态筛选；区分“当前页”与“全部筛选结果”，跨页读取失败不会产生部分全选。
2. 选择操作：基本信息、采购价区间、卖价区间分别处理。修改区间按原开始/结束日期精确定位，缺少对应区间的产品明确列出、不自动新增。
3. 编辑数据：直接在表格逐行或统一填写。批量日期只修改勾选字段，保留各产品原价格；新增期间须分别填写价格，可为同一产品增加多个期间。
4. 核对并保存：服务端验证身份、版本、期间归属、金额、币种、日期、重叠和主数据引用；展示中文逐行错误及旧值/新值。直接批次全批成功才写入，回滚也全批成功才恢复。

旧产品 Excel 上传/导出与图片上传保留；清理旧强制下载再上传专用页面代码，不更改旧 Excel 的逐行容错契约。后端新增 `POST /api/data-upload/workbench/products/prepare-update`，复用暂存、校验、审计、提交和回滚体系，没有新表或迁移。

## 验证证据

- 一次本地完整离线后端：1902 passed / 102 skipped；复核后针对性后端 39 passed，前端 244 项、TypeScript、Turbopack生产构建通过；独立临时 PostgreSQL 并发测试 3 passed；合成25产品跨页选择、24区间日期更新、保存和回滚通过。
- 合并后再次运行后端关键回归 39 passed，前端43文件/244测试、TypeScript、Turbopack生产构建均通过；没有重复运行本地全系统套件。
- 唯一 main CI：后端1906 passed / 104 skipped / 5 warnings，476.39秒；架构0违规、wheel构建安装检查通过；前端测试、类型和生产构建通过。特殊真实LLM/专用PG等跳过项不计通过。
- 制品均由合并提交的 Git archive 构建，排除本地凭据、数据库、依赖和缓存；前端显式设置源码标识 `eacc003`。
- 0%后端候选先验证 `/health` 为目标revision、OpenAPI包含新POST、未登录调用401、正式前端Origin的CORS预检200，再切100%。
- 正式后端健康200且revision正确；正式 `/login`、`/dashboard/workbench/product-update`、原产品上传及数据管理页面均200，新接口鉴权401、CORS200。
- 正式域名通过Vercel inspect解析到新Ready deployment；Oracle Job generation25的镜像与实际运行后端digest一致，三个Scheduler保持启用。
- 通过既有强制只读数据库连接核验 `transaction_read_only=on`、head为0034、产品数量1549，连接与代理已关闭。
- 原浏览器会话过期后，用户重新登录；正式页面显示源码标识eacc003、四阶段和1549产品。大阪筛选169产品，跨页全选实际选中169。
- 基本信息编辑显示169行及现有12个字段；新增采购价页面保留空价格和日期，明确显示中文必填问题，可展开真实旧期间。
- 用真实已有期间2026-05-15至2026-09-30精确定位到121个采购价区间，48个产品明确未找到；编辑页为121行，20800、680等各产品原价和JPY/原日期均正确保留。
- 未调用程序检查的暂存POST、保存或回滚；完成后页面恢复全部产品/已选择0，未触碰另一用户标签页的旧更新草稿。
- 日期控件第一次自动fill只改变可见值，没有触发React输入状态；重新渲染证明状态仍为空。改用原生键盘分段输入后正确命中121区间，属于验证工具输入问题，没有因此修改生产代码。

## 已知债务与用户检查

候选日志仅发现既有passlib/bcrypt兼容告警；CI提示旧GitHub Actions运行时/ubuntu-latest未来变更，未混入本次功能发布。前端ESLint配置仍是已记录独立债务。

用户下一步重点检查真实修改：只改结束日期保留原价格、新增不重叠价格区间及中文错误/旧新值核对。自动生产检查没有创建测试批次或提交更新；第一次真实保存建议使用少量可安全恢复的产品。新版定时扫描首次执行仍待正常调度观察，本轮未为了验收额外手动扫描。

## 回退

- 后端流量切回 `cruise-v3-backend-product-validity-20261001`。
- Oracle Job恢复镜像 `sha256:69c0cf31064c7605e8683f7f26332bf70711f7d1ba60fff027fad4dc4445c87f`。
- 前端重新提升 `dpl_dtbFYWsW1pRU9t1C83JtYSsrrb3P`。
- 无数据库迁移，回退应用不执行数据库downgrade，也不删除审计或历史。
