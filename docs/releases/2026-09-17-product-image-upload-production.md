# 产品图片上传工作台生产发布

日期：2026-09-17（Asia/Tokyo）

## 结论

产品图片上传工作台已部署到正式环境。生产代码为 `main@571813ef227186d7702550c6d5d09e524747e594`，数据库 head 为 0030，正式后端 revision 为 `cruise-v3-backend-image-upload-20260917`，正式前端 deployment 为 `dpl_8QAjk1tYQcqu8rB1Ainn4hiq5Hed`。

## 发布证据

- 合并后后端完整回归：1749 passed、79 skipped、0 failed。
- 前端：12 个测试文件、93 项测试通过；TypeScript 与标准 Next.js 生产构建通过。
- GitHub Actions：`35197032040`，前后端任务均成功。
- Cloud Build：`738e8844-7ca6-48d2-9359-5fa06b57f8c7`，镜像 digest `sha256:2239d2066f9d421099e95be72bc1147702486a8cab1e94fcbb6c86875e7c4920`。
- Cloud SQL 按需备份：`1789633082646`，状态 SUCCESSFUL；迁移任务把 0029 顺序升级到 0030。
- 正式后端 100% 流量指向新 revision；候选阶段已检查健康、OpenAPI、新 API、未认证 401、正式来源 CORS 与 ERROR 日志。
- 正式前端状态 Ready；`/login`、`/dashboard`、`/dashboard/workbench` 和 `/dashboard/workbench/image-upload` 均返回 200。
- Oracle Job 已更新为与后端相同的镜像；三个 Scheduler 均为 ENABLED，手动 Oracle 执行 `cruise-v3-po-hourly-zp6bh` 成功。

## 数据核验

最终只读执行 `cruise-v3-image-upload-preflight-20260917-mw495` 成功，确认数据库 head 0030，并保留 1449 产品、71 订单、31 询价、621 张产品图片、5 个历史图片批次及 8 条历史暂存记录；新顺序计划表为 0 行。迁移没有改写原有产品图片或业务订单。

## 回退边界

应用回退只能选择兼容数据库 0030 的 revision；不要在正式流量下盲目 downgrade 0030。按需备份 `1789633082646` 是灾难恢复边界，数据库恢复属于有数据丢失风险的独立操作，必须重新确认后执行。

## 尚待用户验收

自动化验证没有使用真实账号向生产产品写入图片。最终验收应由用户在正式工作台完成一次真实的“上传文件、程序检查、核对主图与顺序、提交”流程，并确认结果符合实际操作习惯。

## 同日第一轮验收优化

前端提交 `dcb9fec1b61c4c3e8ed690c989ebbb8b8534c97d` 优化图片上传第一步：点击产品后显示完整现有图库和主图标识，上传拖放区改为紧凑布局。修改未涉及第二至第四步、后端、数据库或正式业务数据。

本地前端 96 项测试、TypeScript 和标准生产构建通过，GitHub CI `35202327131` 前后端成功；Vercel `dpl_DidEP3xtoUEVFd8iRzR1RcmZdGwV` 已提升正式域名并为 Ready，正式图片上传页返回 200。后端 revision、镜像、数据库 0030 和三个 Scheduler 状态均沿用本页上一节已经核验的生产基线。

## 同日第二轮验收优化

源码提交 `92bfeabaeb61d45da86cd40fc12b1ecd3a8eb5bf` 将第一步改为全宽业务表格，加入国家、港口、产品代码/名称和“只看暂无图片”筛选；不加入供应商，也不支持整个文件夹上传。选中产品后在原行下方展开完整图库与该产品专用选择入口，本次新增图片直接显示在对应产品行。

首屏改用图片工作台专用轻量接口，每页 40 项，只返回 10 个必要字段，并以固定 2 条 SQL 完成总数和当前页查询；前端去除重复产品请求并延后非关键历史读取。本地后端完整回归为 1737 passed、94 skipped、0 failed，前端 14 个文件、96 项测试通过，TypeScript、架构检查、Ruff 和 Next.js 生产构建通过；GitHub Actions `35231578358` 前后端成功。

Cloud Build `936744d7-1af7-47de-aae3-30ce4862a751` 生成镜像 digest `sha256:dcd039d6f3f54a55b3fbcca26faa4abb488df94cc14afa727cd034198eabde2c`。候选验证后，后端 `cruise-v3-backend-img-table-20260917` 接收 100% 流量；正式前端为 `dpl_BRwUsYCd1tNFrcaeGbHuYUyd4Yep`，状态 Ready。

本轮没有数据库迁移或业务数据写入。只读核验 `cruise-v3-image-upload-preflight-20260917-tq98s` 为 PASS：head 0030，1449 产品、71 订单、31 询价、621 张正式图片、6 个历史批次、9 条历史暂存记录和 1 条历史顺序计划；Oracle Job 与后端使用相同镜像，三个 Scheduler 均保持 ENABLED。
