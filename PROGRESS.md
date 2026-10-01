# 项目进度

更新时间：2026-10-01（Asia/Tokyo）

## 2026-10-01 产品整体有效期退役已在本地完成，尚未部署

- 已从产品 ORM、API、表单、详情、列表和上传审查移除产品整体 `effective_from/effective_to`；产品是否参与匹配只由启用状态、国家和港口决定。
- 新增迁移 `0033_product_validity_backfill` 与 `0034_drop_product_validity`：前者仅填补为空且能形成有效区间的采购价日期，并将所有处理结果写入审计表；后者删除旧列。倒置、不完整、缺价格或区间冲突的历史数据不猜测、不丢弃，可依审计表精确降级。
- 订单匹配已统一使用装船日选择采购价和卖价；装船日未命中采购价期间时，商品仍保持匹配成功并继续生成询价，仅增加“采购价期间未配置/未命中，需要复核”警告，不计入阻塞性待处理。
- 批量上传模板已由 21 列收敛为 19 列；旧表头会明确提示停用。同区间不同价格、文件内重叠和与数据库期间重叠均在写库前列出；回滚会删除本批次创建的价格期间。
- 验证证据：匹配→异常→询价与上传连接回归 `201 passed, 11 skipped`；后端完整回归（含真实 PostgreSQL 迁移升降级）`1877 passed, 101 skipped`；前端完整回归 `226 passed`，TypeScript 与 webpack 生产构建通过。CI 已增加 PostgreSQL 17 service，后续不再静默跳过迁移往返测试。
- **尚未部署：**生产数据库仍为 `0032_llm_port_resolution`，线上前后端和 Oracle Job 仍为 2026-09-29 核验版本。下一步是整体 review、合并与 CI，然后按“备份→0033→应用发布→验证→备份→0034”的顺序发布。

### 本轮已识别但未扩大处理的技术债务

- ESLint 10 已安装，但仓库仍没有 `eslint.config.*`，所以无法单独运行 ESLint；本轮以 Vitest、TypeScript 和生产构建作为前端静态/行为保护，ESLint 配置迁移保留为独立债务。
- 工作树为复用已安装依赖而建立的 `node_modules` 软链接会被 Turbopack 拒绝；同一源码已通过 Next.js webpack 生产构建，正常 CI/Vercel 使用本地安装依赖时不受影响。

## 2026-09-29 产品详情、价格历史与图片页面已正式上线

- 新增稳定产品详情路由 `/dashboard/data/products/{id}?tab=basic|prices|images`：基本信息只展示现有主数据字段；价格页分别管理采购价/卖价区间并保留审计筛选、分页与恢复；图片页保留批量选择/拖放上传、主图标识、查看和删除。
- 新增只读 `GET /api/data/products/{id}`，不存在返回 404；未新增数据库字段或迁移。`employee` 保持可读，产品、价格和图片写操作仍只向现有有权限角色开放。
- 产品列表/图库、PO 商品问题与单位换算规则均已接入新详情路由；旧 `?product={id}&action=edit|prices` 深链由单一兼容映射跳转，不再依赖当前列表分页才能打开产品。
- 已清理产品列表中的三组弹窗状态与 400 余行重复表单逻辑；新增/编辑共用一个表单组件，价格与图片主体改为可嵌入面板。图片排序、手动设主图、AI 图片识别和无关 passlib/crypt 告警未纳入本轮。
- 验证证据：后端产品/价格/图片聚焦回归 `187 passed`，触及后端文件 Ruff 通过；前端完整回归 `226 passed`、TypeScript 通过、Next.js 生产构建通过；后端完整回归 `1874 passed, 101 skipped, 5 warnings`，无失败。
- 生产源码和 GitHub main 均为 `27af29d4b9b5e6935e03e0a25604596ea4633b82`，CI `36577665407` 成功；后端 `cruise-v3-backend-product-detail-20260929` 接收 100% 流量，镜像 digest 为 `sha256:34569bf20a39f88df4877be3b246360570a31fdd82ada7808a559d7416174df2`；前端 `dpl_6b16LdmXSnYdWoAaECgBhyGCy7i7` 为 Production / Ready；Oracle Job generation 23 使用同一镜像并已完成首次自动扫描。数据库保持 `0032_llm_port_resolution`，本轮没有迁移。
- 待人工检查：使用正式权限账号核对 employee/admin/superadmin 三种权限；用真实产品分别检查区间新增/修改/停用、历史恢复、图片上传/查看/删除和旧书签跳转。自动化没有向生产产品写入测试数据；完整证据见根目录 `DEPLOYMENT_VERIFIED_2026-09-29_PRODUCT_DETAIL.md`。

## 2026-09-26 最新上线

- PO 商品明细的供应商列已改为读取当前匹配对应的供应商主数据名称，不再依赖旧询价快照或显示“供应商 #ID”；真实主数据缺失时会明确提示资料缺失。
- 生产源码为 `main@531db856de74bb6b5c01e6b266aaccee208ba695`，PR #10、PR CI `36228224683` 和 main CI `36228653584` 均通过；正式后端 `cruise-v3-backend-supplier-names-20260926` 接收 100% 流量，镜像 digest 为 `sha256:ed36b3e8cd1fcdff1ec6da9728553864b11114db985e8a52ab27ff4e8833daff`。
- 正式前端为 `dpl_BVFDhf8WnSJ9TfbrpVnG3ioA1Eq1`；Oracle Job generation 18 使用同一镜像，三个 Scheduler 仍为 ENABLED。数据库没有迁移并保持 `0031_unit_conversion_rules`。
- 生产只读核验确认 PO165047CCI 仍为 56 行、53 行当前匹配、3 项需处理，并返回三个真实供应商名称；本轮没有重新匹配、修改订单或生成询价。完整证据见 [生产核验](DEPLOYMENT_VERIFIED_2026-09-26_ORDER_SUPPLIER_NAMES.md)。

## 2026-09-24 最新上线

- 数据管理已新增“单位换算规则”界面：正式前端为 `dpl_D1ArQ9TcAjCyXQi38Apy38hEScJr`，源码 `main@c50f322`。页面集中展示规则状态、精确换算关系、适用范围、证据、有效期间和当前审计字段，管理员可填写依据后审核或停用；本轮只部署前端，没有修改数据库、后端或 Oracle Job，证据见 [单位换算规则界面生产核验](DEPLOYMENT_VERIFIED_2026-09-24_UNIT_CONVERSION_UI.md)。
- 商品单位换算第一版已合并并部署：生产数据库 `0031_unit_conversion_rules`，后端 `cruise-v3-backend-uc-on-20260924` 接收 100% 流量，镜像 digest 为 `sha256:fafc1b01f6b360d136ae3bb3c512a00a3164465168ba8fdad02fd198d75f6100`。
- 上一前端 Production 为 `dpl_8VjLndA9QmwgeojV7mZ65bfZmSy4`；Oracle Job generation 16 与后端使用同一镜像并开启单位换算开关，三个 Scheduler 均为 ENABLED。
- 生产只有 5 条候选草稿、0 条已验证规则。PO165047CCI 默认仍有 53 行要求人工确认；显式草稿影子审计可覆盖这 53 行，但发布流程没有自动验证任何业务关系。
- 最终只读基线为 1461 产品、74 订单、32 询价、621 张图片、36 条价格期间。完整证据见 [商品单位换算生产核验](DEPLOYMENT_VERIFIED_2026-09-24_UNIT_CONVERSION.md)。

## 已上线基线

- 截至 2026-09-09，PO 自动获取、文档处理、订单建立、自动归组、供船安排及既有询价流程已在生产运行。
- 当前 GitHub 仓库是从已核验 v3 源码整理出的新基线；旧 v2 Git 历史不用于判断当前生产版本。

## 已在本地完成、尚未部署

- 采购价与卖价拥有各自的开始/结束日期；采购价和卖价均按装船日命中价格区间。
- 工作台模块化改版及产品 Excel 上传四步流程：上传、程序校验、左右变更核对、确认提交。
- 多 PO 供船安排的统一匹配、版本化询价、逐行来源追踪及可扩展异常检测。
- 订单管理、供船安排工作区和 PO 详情页的结构化 UI/UX 改版。
- 日本标准询价单已接入真实 Excel 模板，支持动态商品行、供应商/交付/付款信息和管理端配置。
- 模板顶部为 `PURCHASE ORDER`，第二行为动态“邮轮名［目标港口］”。

## 发布前待办

1. 复核数据库迁移顺序及生产备份，迁移版本须按仓库中的 Alembic 顺序执行。
2. 在候选环境验证标准询价模板的存储初始化与供应商模板关联。
3. 运行后端专项与扩大回归、前端单元测试、类型检查和生产构建。
4. 分别发布后端与前端，再进行真实权限、上传、订单、安排及询价下载验收。
5. 核验成功后更新本文件；当前条目不得提前标记为已上线。

## 仓库安全边界

- `.env`、数据库、上传文件、生成文件和构建缓存不得提交。
- `test-orders/` 含真实业务样本，仅保留在受控本地环境，不进入公开 GitHub。
- GitHub 提交与生产部署严格分离。
