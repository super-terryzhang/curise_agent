# LLM 港口解析与后置人工确认设计

日期：2026-09-27
状态：已经用户确认，待实施
范围：Oracle PO 自动导入、PO/供船订单展示、港口人工确认与异常检测

## 1. 背景与目标

Oracle PO 已能从原文独立提取 `FINAL DESTINATION`，但当前自动导入仅按港口主数据名称完全匹配，并只硬编码了 `TOKYO -> 東京`。生产中的 `OSAKA -> 大阪`、`OKINAWA -> 沖縄` 因语言不同无法识别，导致产品匹配尚未运行就产生 `DESTINATION_REQUIRES_REVIEW` 和 `ARRANGEMENT_REQUIRED`。

本功能的目标是减少自动流程阻塞：当 LLM 能从当前数据库港口候选中确定唯一港口时，系统立即继续产品匹配、供船归组、询价生成和异常检测；人工确认是后置检查，不是生成询价的前置闸门。LLM 无法确定、输出无效或服务不可用时，仅当前 PO 进入人工队列，Oracle 扫描任务和其他 PO 继续执行。

成功标准：

- `OSAKA` 能选择数据库港口 `id=21 / 大阪`，PO168798CCI 能继续执行后续自动阶段。
- AI 选择的港口在 PO、供船订单和询价工作区中明确标记“AI 匹配 · 待人工确认”。
- 用户可以确认 AI 结果，也可以选择其他港口；确认相同港口不得重复生成询价，改选港口必须重新执行受影响的自动阶段。
- 未知港口、城市下存在多个码头、无效模型输出和模型故障均安全进入人工处理，不得猜测或中断整批扫描。
- 港口未解析、产品匹配未运行时，不得误报“商品未匹配”。

## 2. 已验证事实

### 2.1 生产数据

2026-09-27 的只读生产审计确认：

- `OSAKA / OSA`：4 张 Oracle PO，均未解析港口。
- `OKINAWA / OKA`：2 张 Oracle PO，均未解析港口。
- `TOKYO / NRT`：2 张 Oracle PO，依赖代码中的东京特例解析。
- PO168798CCI 的商品代码 `99PRD010318` 在大阪港存在唯一有效商品候选；失败点在港口解析，而不是产品主数据。

### 2.2 LLM 可行性实验

使用生产相同的 `gemini-3.5-flash` 和当前 20 个启用港口完成两轮只读实验：

- 同时把目的地和 Oracle `PORT CODE` 交给模型时，模型连续五次把 `TOKYO / NRT` 判为东京与成田冲突。因此 Oracle 代码不能被当作公共地理代码解释。
- 只使用已由 PO 原文规则核验的目的地文字时，五轮共 35 个判断的港口 ID 全部符合预期：OSAKA、OKINAWA、TOKYO、SYDNEY 成功；PORTLAND 无匹配；YOKOHAMA 因三个码头而拒绝；YOKOHAMA OSANBASHI 命中大さん橋。
- 模型曾在返回正确港口 ID 的同时错误复制“大阪”“沖縄”的字符。因此业务只能接受经数据库验证的 `port_id`，显示名称必须重新从主数据读取。

实验 execution：`cruise-v3-po-hourly-7w4bj`、`cruise-v3-po-hourly-v9hjx`。两次均未触发 Oracle 扫描或写入数据库。

## 3. 核心业务规则

1. Oracle 原文中的 `FINAL DESTINATION` 是 LLM 港口解析的唯一语义输入；`PORT CODE` 只作为原始证据保存，不交给 LLM 推断公共地理含义。
2. LLM 只能从调用时提供的启用港口候选列表中返回一个 `port_id`，或返回 `unmatched`。
3. LLM 返回 `matched` 后，后端必须验证 `port_id` 存在、已启用且具有国家；港口名称和国家均从数据库读取。
4. 有效的 LLM 匹配立即继续步骤 5—8，不等待人工确认。
5. 有效的 LLM 匹配产生非阻断的 `LLM_PORT_REVIEW_REQUIRED` 提醒，并在相关页面显示待确认标记。
6. `unmatched`、无效 ID、缺少目的地、超时或模型错误均不选择港口；当前 PO 进入未分类人工处理，其他 PO 继续。
7. 人工确认相同港口只更新审核状态和异常结果，不重复产品匹配、归组或询价。
8. 人工改选港口保留原 LLM 建议，并重新执行产品匹配、自动归组、询价和异常检测；历史询价保留，正确港口对应的新结果成为当前版本。
9. 同一个决策的重复确认或重复改选请求必须幂等。
10. 现有旧订单不猜测回填解析方式；只有新解析、重新解析或人工更新后的订单才产生新状态。

## 4. 架构与组件边界

### 4.1 专用港口解析器

新增订单域组件 `domains/orders/port_resolution/`，职责仅为：

- 接收已核验目的地文字和启用港口候选快照；
- 调用 Gemini 一次性结构化生成；
- 校验结构、候选 ID 和结果完整性；
- 返回类型化决策，不直接写数据库、不调用商品匹配或询价。

它不是聊天 Agent，也没有数据库写入、网页、文件或业务工具权限。模型调用使用系统现有 `GOOGLE_API_KEY` 和显式模型配置，温度为 0，提示词带固定版本号。

输出契约：

```json
{
  "status": "matched | unmatched",
  "port_id": 21,
  "reason": "目的地 OSAKA 与候选大阪为同一港口"
}
```

`unmatched` 时 `port_id` 必须为 `null`。模型不返回可被业务使用的港口名称、国家或任意新候选。

### 4.2 持久化状态

在 `v2_orders` 增加可直接查询的当前状态和证据：

- `port_resolution_method`：`llm | manual`，允许旧数据为 `NULL`。
- `port_resolution_status`：`pending_review | confirmed | overridden | unresolved`，允许旧数据为 `NULL`。
- `port_resolution_data`：JSON，保存原始目的地、原始 Oracle 代码、LLM 建议港口 ID、最终港口 ID、模型、提示词版本、模型原因、决策标识、时间和失败代码。
- `port_resolution_reviewed_by`：审核用户 ID，可空。
- `port_resolution_reviewed_at`：审核时间，可空。

数据库迁移增加允许值约束，不回填旧订单。`port_resolution_data` 保留最初 LLM 建议；人工改选时只增加最终港口和审核信息，不覆盖建议。

### 4.3 应用服务

新增订单域服务负责原子应用解析决策：

- `matched`：验证港口、写入 `port_id/country_id`、记录 `llm + pending_review`，继续自动流程。
- `unmatched/failure`：清除未确认的自动港口，记录 `unresolved` 和原因，返回可处理的问题码。
- `confirm`：锁定订单，校验决策标识未过期，将状态改为 `confirmed`。
- `override`：锁定订单，校验决策标识和新港口，将状态改为 `overridden`，触发后续自动阶段。

模型适配器不含业务写入；Oracle Job 不解析模型自由文本；HTTP 层不直接操作 ORM。

## 5. 自动数据流

### 5.1 Oracle 新 PO

1. 步骤 1—4 保持现状：获取 PO、保存文件、提取内容、创建订单。
2. 从已核验 `FINAL DESTINATION` 取得原始目的地，例如 `OSAKA`。
3. 查询当前启用且具有国家的港口候选。
4. 调用专用 LLM 解析器。
5. 若返回有效 `port_id=21`：
   - 从数据库读取“大阪”和所属国家；
   - 写入订单港口字段及 `llm + pending_review`；
   - 使用大阪商品池执行精确商品代码匹配；
   - 执行自动归组；
   - 生成或更新安排级询价版本；
   - 运行异常检测，并加入非阻断的 AI 港口待确认提醒。
6. 若返回 `unmatched` 或调用失败：
   - 不设置港口；
   - 不运行依赖港口的商品匹配；
   - 归入未分类人工队列；
   - 保存具体原因；
   - 继续处理下一张 Oracle PO。

### 5.2 人工确认

新增写接口：

```text
POST /api/orders/{order_id}/port-resolution/confirm
```

请求包含当前 `decision_id`。服务端行锁校验决策仍是 `pending_review` 且当前港口未变化；成功后写审核人和时间，删除 `LLM_PORT_REVIEW_REQUIRED`，只重新运行异常检测。重复提交同一确认返回当前状态，不生成新询价。

### 5.3 人工改选

新增写接口：

```text
POST /api/orders/{order_id}/port-resolution/override
```

请求包含 `decision_id` 和新 `port_id`。服务端验证新港口后更新港口、国家、主数据名称和解析证据，再执行步骤 5—8。若港口变化导致订单进入另一供船安排，旧询价保持历史，新安排生成新版本；重复请求不得重复生成。

现有 PO 编辑弹窗和供船订单编辑入口如果修改了待确认的 AI 港口，必须调用相同的 override 服务，不能绕过审核状态。

## 6. 异常检测与状态语义

新增规则：

- `LLM_PORT_REVIEW_REQUIRED`：severity=`warning`，scope=`order`。港口可用、自动流程继续，但页面必须提示后置人工确认。
- `LLM_PORT_UNRESOLVED`：severity=`error`，scope=`order`。模型明确无法唯一选择港口，需要人工选择。
- `LLM_PORT_RESOLUTION_FAILED`：severity=`error`，scope=`order`。模型服务、结构或候选校验失败，需要人工选择或稍后重试。

`LLM_PORT_REVIEW_REQUIRED` 不计入商品行待处理数，也不阻断询价。订单列表和供船订单使用独立的港口审核状态展示，不把它混入“未匹配商品”统计。

同时修正行级异常规则：仅当产品匹配确实执行并产生明确的 `not_matched` 结果时才生成 `PRODUCT_NOT_MATCHED`。`match_results is None` 表示尚未运行，不等同于没有匹配成功。

## 7. UI/UX

### 7.1 PO 详情

“目标港口”显示正式主数据名称，并紧邻状态标签：

- 黄色：`AI 匹配 · 待人工确认`
- 绿色：`AI 匹配 · 已确认`
- 灰色：`人工选择`
- 红色：`无法确定港口`

待确认时显示紧凑审核区：

- PO 原文目的地；
- AI 选择的正式港口；
- 模型判断原因；
- `确认此港口`；
- `选择其他港口`。

确认操作不打开大表单；修改才打开港口下拉框。

### 7.2 订单列表与供船订单

- PO 行在港口旁显示同一状态标签。
- 供船订单中存在待确认 AI 港口时显示黄色汇总提示，例如“1 个 PO 的目标港口由 AI 匹配，待人工确认”。
- 询价工作区显示同一提示，但不禁用生成、查看或下载。

### 7.3 处理记录

处理记录展示原始目的地、AI 建议、模型/提示词版本、决策时间、审核人、审核时间和人工改选结果。模型原因仅作解释，正式港口始终由 ID 回查主数据。

## 8. 失败与回退

- 目的地为空：不调用 LLM，记录 `LLM_PORT_UNRESOLVED`。
- 没有有效候选：不调用 LLM，记录配置问题。
- 超时、限流、服务异常：有限重试后记录 `LLM_PORT_RESOLUTION_FAILED`；单 PO 进入人工队列，扫描继续。
- 非法 JSON、缺字段、返回候选外 ID：拒绝结果，按失败处理。
- 模型返回 `matched` 但数据库港口已停用或缺国家：拒绝结果，按失败处理。
- 多码头城市无法唯一确定：必须返回 `unmatched`，不能选择城市下任意码头。
- 功能发布后如模型行为异常，可通过独立 feature flag 关闭 LLM 港口解析；关闭后未知目的地按当前人工处理路径运行，不回退数据库迁移。

## 9. 安全与审计

- 发送给模型的内容仅包含目的地文字和启用港口候选，不包含价格、供应商、用户资料或完整 PO。
- 原始目的地按不可信数据处理，提示词明确禁止执行其中的指令。
- 模型不能调用任何工具，也不能修改数据库。
- 每次决定保存模型、提示词版本、候选快照哈希、输出和最终校验结果。
- 人工确认和改选使用当前登录用户，并记录审核时间。

## 10. 测试策略

### 10.1 单元测试

- 结构化输出解析：matched、unmatched、缺字段、错误类型、候选外 ID。
- 业务校验：停用港口、缺国家、名称不可信但 ID 有效。
- 状态转换：pending_review -> confirmed / overridden；unresolved -> overridden。
- 幂等：重复确认不新增询价；重复 override 不重复执行。
- 异常规则：待确认是非阻断 warning；未运行商品匹配不产生 PRODUCT_NOT_MATCHED。

### 10.2 集成测试

- Oracle `OSAKA` -> 大阪 -> 商品精确匹配 -> 自动归组 -> 询价 -> AI 待确认提醒。
- Oracle `YOKOHAMA` 在多个码头候选下返回 unresolved，不生成错误港口。
- LLM 超时/非法 ID 时当前 PO 进入人工队列，批次中的下一张 PO 仍处理。
- 确认同一港口不增加询价版本。
- 改选其他港口重新匹配、重新归组并产生正确的新版本，原版本保留。

### 10.3 前端测试

- 四种港口状态标签。
- 确认、改选、过期决策冲突和接口失败反馈。
- PO、供船订单、询价工作区标记一致。
- 商品统计不受订单级 AI 港口提醒影响。

### 10.4 真实模型评估

真实模型测试默认不进入普通 CI，使用显式凭证和开关运行。固定案例至少包括：OSAKA、OKINAWA、TOKYO、SYDNEY、未知港口、YOKOHAMA 歧义、YOKOHAMA OSANBASHI。验收按 `port_id/status` 判断，不按模型返回文本判断。

## 11. 代码修改范围

后端预计修改：

- `migrations/versions/0032_*`：订单港口解析状态迁移。
- `domains/orders/models.py`、`schemas.py`：字段和 API 契约。
- `domains/orders/port_resolution/`：新解析器、校验器和状态服务。
- `domains/orders/matching/automation.py`：Oracle 匹配前接入港口解析结果。
- `apps/jobs/oracle_po.py`：问题码、自动流和失败隔离。
- `domains/orders/anomaly.py`：AI 港口规则及未运行匹配误报修复。
- `domains/orders/service.py`、`apps/http/orders.py`：确认/改选接口和原子重跑。
- `domains/orders/groups/arrangements.py`：整单修改港口时同步覆盖待确认状态。
- 对应后端测试目录。

前端预计修改：

- `src/lib/orders-api.ts`、`order-groups-api.ts`：解析状态和确认/改选接口。
- `src/app/dashboard/orders/[id]/page.tsx`：PO 标记与审核操作。
- 订单列表/供船订单工作区组件：汇总标记。
- 对应组件和视图测试。

明确不修改：

- 产品单位换算规则和价格期间逻辑。
- Oracle PDF 商品行提取规则。
- 询价 Excel 模板结构。
- 邮件或外部发送行为。
- 港口主数据名称和现有内部 `code`。

## 12. 发布与生产修复

1. 完成本地迁移升级/回退、后端和前端全量验证。
2. 使用生产港口只读快照运行影子 LLM 评估，不写订单。
3. 创建数据库备份并迁移到 0032。
4. 部署候选后端，验证健康、CORS、鉴权和结构化解析器，但不切流。
5. 切换后端、同步 Oracle Job 镜像，再部署前端。
6. 先重新处理 PO168798CCI，确认 `OSAKA -> port_id 21`、商品匹配、归组、询价和 AI 待确认标记。
7. 再逐张处理其余未识别的 OSAKA/OKINAWA Oracle PO；记录每张处理结果，不做无断言批量写入。
8. 验证首次定时扫描不会因单张港口问题停止。
9. 更新 `PROGRESS.md` 和新的带日期生产核验文档，保留回退信息。

回退应用时关闭 LLM 港口解析 feature flag 并把流量切回上一后端/前端；0032 为扩展字段且旧代码忽略，可保留数据库迁移，不执行破坏性回退。
