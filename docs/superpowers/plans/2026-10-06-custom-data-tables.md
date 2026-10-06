# 自定义数据表与设置页面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for native task-by-task implementation. 用户尚未选择执行方式；不得启动实现或自行派发子代理。若用户选择分工，先按 superpowers:subagent-driven-development 的要求执行。

**Goal:** 交付设置中心可操作的自定义数据表页面，支持建表、配置列、录入编辑、关联、校验及修改历史。

**Architecture:** 新增独立 dynamic_data 领域，通过六张固定物理表保存逻辑表、字段、记录、关联、唯一值和历史。动态类型由服务端统一校验，固定 ID 保持身份，版本和事务保护保存；前端根据定义生成控件，不替换原有产品或订单业务。

**Tech Stack:** FastAPI、Pydantic、SQLAlchemy、Alembic、PostgreSQL、pytest；Next.js、React、TypeScript、现有 shadcn 组件、Vitest。实际事件测试新增 jsdom 26.1.0、Testing Library React 16.3.3、DOM 10.4.2、user-event 14.6.7 开发依赖，不改变生产依赖。

**Spec:** [自定义数据表与设置页面交付设计](../specs/2026-10-06-custom-data-tables-design.md)。2026-10-06 用户回复“继续”，确认书面设计；本计划待审阅及执行方式确认。

## Global Constraints

- 本轮不改变现有 products、suppliers、订单、价格期间或单位换算表；关联目标限本轮创建的自定义表。
- 不包含附件、计算公式、Excel 导入导出、批量编辑、历史一键回滚、独立表级授权或任意 SQL 执行。
- 第一版支持文本、数字、日期、日期时间、单选、多选、是或否、关联记录共八种类型。
- 数字最多 18 位有效数字和 6 位小数，使用 Decimal 和十进制字符串；日期 YYYY-MM-DD，日期时间必须带时区，界面使用 Asia/Tokyo。
- 每张表最多 100 个字段，每个选择字段最多 100 个选项；单行普通值不超过 64 KiB；分页默认 50、最大 100。
- 唯一配置限文本和数字；唯一文本最长 200 字符、区分大小写；归档记录保留唯一值。
- 管理员管理结构；Writer 即 superadmin/admin/employee/finance 管理记录并读取记录和历史；其他角色返回 403。
- 固定 UUID 为身份，PATCH 缺失保持、null 清空；系统 ID、时间和操作人不可编辑。
- 不提供物理删除；归档保留值和历史；没有记录历史回滚按钮。
- 当前本地 Alembic head 已核对为 0034_drop_product_validity；生产 head 必须在发布时另行核对，不以本地代替生产。
- 每任务先测试失败再实现，针对性验证后提交；稳定候选一次本地全量和一次 GitHub 完整 CI，不逐任务重复全量。
- 独立工作树执行，保留已有两个本地文档提交；生产写入、推送、合并和发布按照各阶段授权执行。

## Review Focus

- 保存请求已成功但响应丢失或鉴权重试：同一个 UUID 重试不产生第二条数据或第二条创建历史，原请求不能被后续编辑误判；Task 4 和 8。
- 编辑其他字段时含已停用选项或已归档字段：未改的历史值保留，未知新字段和主动提交已归档字段被拒绝；Task 2 和 4。
- 结构修改与记录保存同时发生：不能按旧规则保存新结构数据，也不能因锁升级死锁；Task 7。
- 同名记录、字段改名、关系环和关联搜索响应乱序：使用固定 ID，不自动合并、不递归展开、不采用过期查询结果；Task 5 和 10。
- 全部写入模块未启用或迁移 head 不一致：旧业务仍可运行，但新模块不能暴露半可用写入口；Task 12。

## 文件分工和公共契约

所有路径相对 `curise_agent/`。后端新模块文件：`v3_backend/domains/dynamic_data/{__init__,models,schemas,errors,validation,repository,history,structures,records,lifecycle,query,service}.py`。models 定义六模型，schemas 定义请求和响应，validation 仅纯校验，repository 仅查询和锁，history 仅追加历史，structures/records/lifecycle 是独立业务职责；service 只重导出公开接口。

新增 HTTP 文件 `v3_backend/apps/http/data_tables.py`；修改 `main.py`、`migrations/env.py`、`test_v2/conftest.py` 注册模型和路由。新增迁移 `migrations/versions/0035_custom_data_tables.py`，revision=`0035_custom_data_tables`，down_revision=`0034_drop_product_validity`；实施前再次检查无新分支 head，如有变化停止调整依赖后再执行。

新增后端测试 `test_v2/unit/dynamic_data/test_validation.py`、`test_v2/integration/dynamic_data/{conftest,test_models,test_structures,test_records,test_lifecycle,test_query,test_api,test_postgres,test_migration}.py` 和 `test_v2/e2e/test_custom_data_tables.py`。不搬现有 PO 测试；保持唯一 test_v2 根目录。

前端新增 `src/lib/{data-tables-types,data-tables-api,data-tables-view}.ts`，各自同名 `.test.ts`；新增 `src/components/settings/data-tables/{table-list,table-detail,field-editor,fields-panel,record-editor,records-panel,linked-record-picker,history-panel}.tsx` 及同名测试。路由为 `src/app/dashboard/settings/data-tables/page.tsx` 和 `src/app/dashboard/settings/data-tables/[tableId]/page.tsx`；修改设置页入口，不重做现有字段管理。

公共 schemas 使用以下名称：Actor(id:int,role:str)；TableCreate(id:UUID,name:str,description:str|None)；TableUpdate(expected_schema_version:int,name?,description?,display_field_id?)；FieldCreate(id:UUID,label:str,field_type,required:bool,unique:bool,default_value,config:dict,target_table_id:UUID|None,expected_schema_version:int)；FieldUpdate 使用相同可修改项和 expected_schema_version；FieldReorder(field_ids:list[UUID],expected_schema_version:int)。RecordCreate(id:UUID,schema_version:int,values:dict[str,Any])；RecordUpdate(expected_revision:int,schema_version:int,values:dict[str,Any])；SchemaAction(expected_schema_version:int)；RecordAction(expected_revision:int,schema_version:int)。全部请求 extra=forbid。

响应名称为 TableResponse、FieldResponse、RecordResponse、ChangeResponse、Page[T](items,total,page,page_size)。RecordResponse 包含 id、table_id、values（包含合并的关联 ID）、revision、schema_version、status、系统时间和 display_label。纯校验输出 FieldDefinition，含 id、label、field_type、required、unique、default_value、config、target_table_id，不含数据库时间和操作人；FieldResponse 继承这些定义并加 table_id、status、sort_order、schema_version 和时间。ErrorIssue 含 table_id、record_id?、field_id?、field_label?、code、message；DataTableError 含 code、message、issues。

FieldType 固定为 text、number、date、datetime、single_select、multi_select、boolean、link。config 用受控 Pydantic 类型验证，不接受自由执行表达式。数字配置 precision 1..18、scale 0..6 且 scale<=precision；文本 max_length 1..4096 和 multiline；选择项含 UUID id、label、active；关联只有 target_table_id，不重复放 config。default_value 校验复用类型规则，关联默认值第一版禁止，避免保存长期失效目标。

任务命令除前端外均从 `v3_backend/` 执行，使用 `.venv/bin/python`；前端从 `v3-frontend/` 执行 pnpm。阶段验证结果写入 `docs/custom-data-tables-2026-10-06/PROGRESS.md`，含命令、版本、结果及下一步；最终证据写 `VERIFICATION.md`，不得保存凭据。

## Task 1 存储结构和请求契约

**Files:** 新增 models、schemas、errors、__init__、迁移和 integration fixture/test_models；修改模型注册点与本轮 PROGRESS。
**Interfaces:** 输出 DataTable、DataField、DataRecord、DataLink、DataUniqueValue、DataChange；上一节所有请求和响应类型；模型 JSON().with_variant(JSONB(),"postgresql")，UUID 使用 SQLAlchemy Uuid(as_uuid=True)。

- [ ] 写 `test_six_models_register_and_keep_existing_product_columns`：`assert len(custom_tables) == 6`、`assert product_columns_after == product_columns_before`；`test_links_cannot_use_other_tables_field_or_target` 用有效但错表的 ID 断言 IntegrityError，SQLite 测试连接启用 foreign_keys；`test_requests_forbid_unknown_system_fields` 断言 record 的 created_at 被拒绝。
- [ ] 运行 `.venv/bin/python -m pytest test_v2/integration/dynamic_data/test_models.py -q`，确认缺模块或缺约束失败，不把测试语法错误当 RED。
- [ ] 实现六模型、复合 FK/UNIQUE/CHECK、注册和 schemas；定义 `DataTableError(code:str,message:str,issues:list[ErrorIssue])` 以及 ValidationError、Conflict、NotFound、Forbidden 子类。表/列标签非空，名称最长 100、说明最长 2000。所有版本初始 1，历史只追加。
- [ ] 同命令 PASS；运行 `.venv/bin/python -m alembic heads` 只出现新单 head、`.venv/bin/python scripts/check_arch.py` PASS。禁止此时连接生产运行迁移。
- [ ] 仅提交该任务明确文件，消息 `feat: add custom data table storage contracts`；记录 ORM 校验不是生产迁移证据。

## Task 2 类型校验和规范化

**Files:** 新增 validation.py、unit test_validation.py。
**Interfaces:** `validate_field_definition(field:FieldCreate|FieldUpdate,current:FieldResponse|None)->FieldDefinition`；`normalize_value(field:FieldResponse,value:Any,*,allow_inactive_option_ids:set[str]|None=None)->Any`；`normalize_record(fields:list[FieldResponse],values:dict[str,Any],*,existing:dict[str,Any]|None=None,create:bool)->dict[str,Any]`；`unique_value(field:FieldResponse,value:Any)->str|None`。

- [ ] 写 `test_number_is_canonical_without_float`：`assert normalize_value(number_field, "1.00") == "1"`、`assert normalize_value(number_field, 0) == "0"`；`test_false_is_required_boolean_value`：`assert normalize_value(required_bool_field, False) is False`。其余参数化断言 true 作为数字失败、NaN 失败、7 位小数失败、2026-13-40 失败、无时区 datetime 失败、未知字段失败、行 65537 字节失败；选择重复 ID 与重复多选值失败，100/101 字段及选项边界分别通过/失败。
- [ ] 运行 `.venv/bin/python -m pytest test_v2/unit/dynamic_data/test_validation.py -q` 确认缺实现失败。
- [ ] 实现上述纯函数；Decimal 禁止科学格式产生超界或超长输出，规范化 -0 为 0；PATCH 合并但禁止显式修改归档字段，原归档值留在 existing；停用选项仅允许保持同字段原值。新增默认值仅应用到未提供字段；link 值解析为 UUID，数据库目标检查留 Task 4。
- [ ] 同命令 PASS，补断言 text 不去掉有意义空格、必填空白失败、null 与未提交不同、date 不换日、JST datetime 正确变为 UTC、收紧精度拒绝旧非法值；运行 Ruff 检查触及文件。
- [ ] 提交 `feat: validate configurable field values`。

## Task 3 表与字段配置业务

**Files:** 新增 repository.py、history.py、structures.py、service.py、test_structures.py；继续完善 schemas。
**Interfaces:** 公开 `create_table(db:Session,body:TableCreate,*,actor:Actor)->TableResponse`、`update_table(db,table_id:UUID,body:TableUpdate,*,actor)->TableResponse`、`create_field(db,table_id,body:FieldCreate,*,actor)->FieldResponse`、`update_field(db,table_id,field_id:UUID,body:FieldUpdate,*,actor)->FieldResponse`、`reorder_fields(db,table_id,body:FieldReorder,*,actor)->list[FieldResponse]`；history `append_change(db,*,table_id,entity_type,entity_id,action,before,after,actor,schema_version,revision=None,creation_request=None)->None` 不提交。

- [ ] 写 `test_rename_preserves_field_id_and_values`：`assert renamed.id == original.id`、`assert persisted_record.values == original_values`；`test_required_and_unique_changes_check_existing_rows` 断言缺值或重复值拒绝且结构版本不变；`test_employee_cannot_change_structure` 断言 Forbidden；`test_reorder_is_atomic_and_preserves_archived_fields` 断言完整 active ID 集合且无重复。
- [ ] 运行 `.venv/bin/python -m pytest test_v2/integration/dynamic_data/test_structures.py -q` 确认缺服务失败。
- [ ] 实现排他结构锁、Actor 角色检查、目标表和显示字段检查、版本冲突、创建重试、字段历史快照；结构变更严格增加一次版本。repo 在本任务定义 `lock_tables(db,table_ids:list[UUID],*,exclusive_ids:set[UUID])->list[DataTable]`；涉及关联目标时按全体 UUID 顺序获取来源排他和目标共享锁，同表只取一个排他锁，不能锁定来源后再反序追加目标。已有任意行时禁止改类型和目标表；任何必填收紧扫描启用行，唯一开启扫描含归档行，在同事务建立唯一值。历史使用当时的定义而非今天的显示名。
- [ ] 同命令 PASS；补测试唯一文本超过 200 字符拒绝、已有数据的新必填字段不能靠默认值悄悄补、101 字段拒绝、结构重复创建 UUID 不追加历史、过期结构版本不覆盖。类型更改只允许空表并更新约束关系。
- [ ] 提交 `feat: manage custom table definitions safely`。

## Task 4 记录保存与关联和唯一事务

**Files:** 新增 records.py、test_records.py；完善 repository/history/service。
**Interfaces:** `create_record(db,table_id,body:RecordCreate,*,actor:Actor)->RecordResponse`、`update_record(db,table_id,record_id:UUID,body:RecordUpdate,*,actor)->RecordResponse`；复用 Task 3 的 `lock_tables`，记录写入 exclusive_ids 为空；repo 新增 `lock_records(db,record_ids:list[UUID],*,write_ids:set[UUID])->list[DataRecord]` 按 UUID 排序；`get_record_values(db,record:DataRecord)->dict[str,Any]` 合并 links。

- [ ] 写 `test_patch_is_partial_but_validates_full_record`、`test_duplicate_value_rolls_back_record_links_and_history`、`test_wrong_target_table_and_archived_target_are_rejected`，失败后 `assert snapshot_after == snapshot_before`；`test_same_create_uuid_after_later_edit_uses_original_request`：`assert retried.id == original.id`、`assert retried.revision == edited.revision`、`assert creation_history_count == 1`。
- [ ] 运行 `.venv/bin/python -m pytest test_v2/integration/dynamic_data/test_records.py -q` 确认失败。
- [ ] 实现共享结构锁、完整值合并规范化、版本检查、关联目标行锁、唯一占位变更和历史同事务；links 不重复保存到 values。先预读关系配置和旧值确定所需锁集合，取锁后重新读取并核对结构/记录版本；若所需集合改变返回冲突，不能已有锁后反序补锁。POST 创建请求快照存原始规范化请求及 actor，重试同 ID 不重新应用当前默认值；不同内容或 actor 冲突。已有 UUID 并发插入 IntegrityError 后回滚再读取原创建历史核对，不能直接视为成功。
- [ ] 同命令 PASS；故障注入 append_change 抛错验证全回滚，未改停用选项保留、改成其他停用选项拒绝；结构更新时间在普通保存后不变化；过期 revision 返回 Conflict，其他角色禁止写；持久化历史无更新入口。
- [ ] 提交 `feat: save custom records with atomic links and history`。

## Task 5 归档恢复与服务端查询

**Files:** 新增 lifecycle.py、query.py、test_lifecycle.py、test_query.py；完善 repository/service。
**Interfaces:** `set_table_status(db,table_id,body:SchemaAction,*,active:bool,actor)->TableResponse`；`set_field_status(db,table_id,field_id,body:SchemaAction,*,active:bool,actor)->FieldResponse`；`set_record_status(db,table_id,record_id,body:RecordAction,*,active:bool,actor)->RecordResponse`。查询 `list_tables(db,*,status:str,page:int,page_size:int)->Page[TableResponse]`、`get_table(db,table_id)->TableResponse`、`list_fields(db,table_id,*,include_archived:bool)->list[FieldResponse]`、`get_record(db,table_id,record_id)->RecordResponse`、`list_records(db,table_id,query:RecordQuery)->Page[RecordResponse]`、`list_changes(db,table_id,query:ChangeQuery)->Page[ChangeResponse]`、`search_link_targets(db,table_id,field_id,*,q:str,page:int,page_size:int)->Page[RecordResponse]`。

- [ ] 写 `test_archived_data_keeps_values_unique_claims_and_history`；`test_target_archive_is_blocked_then_source_archive_allows_it`；`test_restore_with_invalid_target_is_rejected_atomically`；`test_numeric_order_is_two_before_ten`：`assert [row.values[number_key] for row in page.items] == ["2", "10"]`；`test_duplicate_display_names_keep_separate_ids`；`test_cyclic_links_do_not_expand_recursively`。
- [ ] 运行 `.venv/bin/python -m pytest test_v2/integration/dynamic_data/test_lifecycle.py test_v2/integration/dynamic_data/test_query.py -q` 确认缺服务失败。
- [ ] 实现归档及恢复规则、引用查询、保留唯一值、受控类型筛选和分页排序；RecordQuery 字段为 status、page、page_size、sort_field_id?、sort_direction asc|desc、filters 列表。过滤最多 10 项，AND 连接；text eq/contains，number/date/datetime eq/lt/lte/gt/gte，boolean/single_select eq，multi_select contains，link eq，所有类型 is_empty，操作符和值验证后参数绑定。排序支持系统创建时间及标量字段，缺值统一在最后，以记录 ID 作为第二排序键。
- [ ] 同命令 PASS；补测试非法操作符或不存在字段拒绝、q 中引号和 SQL 文本不执行、归档字段和目标显示名回退、51 行分页无遗漏、history 按 created_at/id 倒序、单表计数不产生逐行 N+1。记录新增及记录恢复必须有启用字段，表本身可以先创建或恢复为空表；恢复只验证将启用的约束和引用。
- [ ] 提交 `feat: query and archive custom data without deleting history`。

## Task 6 受保护接口和错误契约

**Files:** 新增 apps/http/data_tables.py、test_api.py；修改 main.py；service 提供上述唯一公开接口。
**Interfaces:** `/api/data-tables` GET/POST；`/{table_id}` GET/PATCH；`/{table_id}/fields` GET/POST；`/{table_id}/fields/{field_id}` PATCH；`/{table_id}/fields/reorder` POST；`/{table_id}/records` GET/POST；`/{table_id}/records/{record_id}` GET/PATCH；`/{table_id}/fields/{field_id}/targets` GET；`/{table_id}/changes` GET。表、字段和记录各提供 `/archive`、`/restore` POST，body 分别 SchemaAction/RecordAction。固定路径 reorder 在可变 UUID 路径前注册。

- [ ] 写 `test_roles_and_direct_url_enforce_structure_and_record_permissions`，验证全部角色，employee 创建表时 `assert response.status_code == 403`；`test_validation_returns_chinese_field_issues` 断言 `assert issue["field_id"] == str(number_field.id)`、`assert "数字" in issue["message"]`；`test_cross_table_ids_are_rejected`；`test_no_delete_or_history_patch_endpoint`。
- [ ] 运行 `.venv/bin/python -m pytest test_v2/integration/dynamic_data/test_api.py -q` 确认 404 或契约失败。
- [ ] 实现 Admin/Writer 依赖、由可信 User 构造 Actor、领域异常 HTTP 转换；输入 ValidationError 仅本路由转换成结构化中文 422，不修改全站错误契约。未知系统字段不能利用 values 修改系统列，未预期错误日志不含完整用户记录。
- [ ] 同命令 PASS，`.venv/bin/python scripts/check_arch.py` PASS；补测试 未登录401、viewer403、无记录404、旧版本409、错误保存不产生历史、POST鉴权重试保持UUID。OpenAPI 逐路径核对，不凭页面按钮存在判断接口可用。
- [ ] 提交 `feat: expose protected custom data table API`。

## Task 7 PostgreSQL 并发和真实迁移验证

**Files:** 新增 test_postgres.py、test_migration.py，专用 fixture；修改 `.github/workflows/ci.yml` 为专项提供环境，新增验证记录。
**Interfaces:** fixture 读取 `DATA_TABLES_TEST_DATABASE_URL`，只接受 PostgreSQL 127.0.0.1、端口 55447 和库 cruise_data_tables_test，或 CI 5432/cruise_migration_test；不接受 Cloud SQL、socket、任意 _test 后缀或生产代理。未配置时明确 skip，交付不得把 skip 当通过。

- [ ] 写并发测试 `test_same_unique_value_has_one_winner`，两个线程共用 Barrier 启动，`assert sum(successes) == 1`、`assert record_count == creation_history_count == 1`；`test_schema_writer_waits_for_record_reader`、`test_link_save_and_target_archive_cannot_both_succeed`、`test_two_updates_cannot_overwrite_revision` 使用 Event/超时控制而非猜 sleep；真实 numeric 排序和类型约束均断言结果。
- [ ] 在专用临时 PostgreSQL 运行 `.venv/bin/python -m pytest test_v2/integration/dynamic_data/test_postgres.py test_v2/integration/dynamic_data/test_migration.py -q`，先确认反例能失败；数据库资源仅本轮专用，不连接或重置生产。
- [ ] migration 测试分两类：新模块 ORM 空库建模；已有 0034 schema snapshot 排除六新表、seed 原产品与期间、stamp 0034 后执行真实 `alembic upgrade 0035_custom_data_tables`。比较前后既有表行内容与列，新六表类型和外键存在；用该迁移的独立空库 DDL 演练验证新模块安装。当前 0001_baseline 是复用旧表的 no-op，不能把空库全历史 Alembic 可安装写成已通过，不扩范围重建旧 baseline。
- [ ] 同命令全部 PASS，无关键 skip；CI 使用已有 PostgreSQL 服务串行运行，不在其他 migration test 运行时并发重置同一库；增加至少六项上述 race/迁移断言和 EXPLAIN 记录。改正失败后只重跑相关集成，记录锁策略与性能边界。
- [ ] 提交 `test: verify custom data constraints and migration on postgres`，停止本轮临时数据库并记录可复现命令，不删除任何非测试库。

## Task 8 前端类型与接口和状态模型

**Files:** 新增 lib 三文件和对应测试。
**Interfaces:** types 镜像后端 snake_case 类型，UUID=string，数字值保持 string。api 输出 listTables/createTable/getTable/updateTable/listFields/createField/updateField/reorderFields/listRecords/getRecord/createRecord/updateRecord/archiveTable/restoreTable/archiveField/restoreField/archiveRecord/restoreRecord/listChanges/searchLinkTargets，参数及返回镜像 Task 3–6；`DataTablesApiError` 保留 code/message/issues/status；所有 fetch 使用既有 fetchWithAuth。

- [ ] 写 API 测试断言 path、UUID、revision、schema_version、detail issues 完整保留；`test_create_retry_keeps_id_after_timeout`：`expect(secondPayload.id).toBe(firstPayload.id)`；view 测试 bool false、数字0、PATCH缺失与null、JST日期时间、未知字段提示。
- [ ] 从前端运行 `pnpm test -- src/lib/data-tables-api.test.ts src/lib/data-tables-view.test.ts` 确认缺实现失败。
- [ ] 实现上述 API、`fieldControlKind(field)`、`displayValue(field,value)`、`buildRecordValues(fields,draft,original)`、`fieldIssues(error)`、`canManageStructure(role)`、`canManageRecords(role)`、`parseTableTab(value)`；不在前端重写权威 Decimal/唯一/目标关系验证。表单编辑状态从当前响应 UUID 键建立，提交不含未改归档字段。
- [ ] 同命令 PASS；数字不得 Number()，错误不能只 throw new Error 丢失 issues；保存响应不明时显示“保存结果暂时无法确认，请先核对，勿创建重复记录”，读取当前记录辅助核对，不能宣称必然失败或自动覆盖。
- [ ] 提交 `feat: add typed custom data table client`。

## Task 9 设置入口与结构管理页面

**Files:** 新增 table-list/table-detail/field-editor/fields-panel、两路由及组件测试；修改 `src/app/dashboard/settings/page.tsx`；修改 package.json、pnpm-lock.yaml，新增 `src/test/setup-dom.ts`。
**Interfaces:** `<TableList />` 从 API 读取；`<TableDetail tableId:string activeTab:"fields"|"records"|"history" />` 管理当前表和字段快照；`<FieldsPanel table:TableResponse fields:FieldResponse[] onChanged:()=>void />`；`<FieldEditor table:TableResponse field?:FieldResponse onSaved:()=>void onCancel:()=>void />`。入口文字“自定义数据表”，现有字段管理增加订单提取用途说明，不破坏旧模板入口。

- [ ] 执行 `pnpm add -D -E @testing-library/react@16.3.3 @testing-library/dom@10.4.2 @testing-library/user-event@14.6.7 jsdom@26.1.0`，仅组件交互测试用文件级 `@vitest-environment jsdom`；保持默认 node，setup 不全局改变现有测试。写实际点击“新建表”、选择类型、输入名称、保存、编辑、上移/下移及权限测试，保存断言 `expect(createTable).toHaveBeenCalledWith(expect.objectContaining({name:"检验记录"}))`，不能只测试 renderToStaticMarkup。
- [ ] 运行 `pnpm test -- src/components/settings/data-tables/table-list.test.tsx src/components/settings/data-tables/fields-panel.test.tsx src/components/settings/data-tables/field-editor.test.tsx`，确认缺页面或行为失败。
- [ ] 实现 PageHeader、统一标签、表单、字段类型相关设置、默认值、选项编辑和停用、目标表选择、显示名称字段、结构版本提交、归档及恢复确认；空状态和无权限提示齐全。按指定版本冻结锁文件，安装后检查 peer warning；兼容性失败先报告并调整计划，不升级整个项目依赖。
- [ ] 同命令 PASS；补 server error 保留输入、无记录才允许改类型、已有记录说明、必填失败列出受影响记录、归档不叫物理删除、外部链接按固定UUID；`pnpm exec tsc --noEmit` PASS。
- [ ] 提交 `feat: manage custom table structure in settings`。

## Task 10 数据编辑与关联和历史页面

**Files:** 新增 record-editor/records-panel/linked-record-picker/history-panel 及组件事件测试；接通 table-detail。
**Interfaces:** `<RecordEditor table:TableResponse fields:FieldResponse[] record?:RecordResponse onSaved:()=>void onCancel:()=>void />`；`<LinkedRecordPicker tableId:string fieldId:string value:string|null onChange:(id:string|null)=>void disabled?:boolean />`；`<RecordsPanel table fields onChanged />`；`<HistoryPanel tableId:string />`。详情 query 参数 tab=fields|records|history、record=<UUID> 支持关联记录深链接打开编辑或只读表单。

- [ ] 写真实事件测试：八控件输入并保存正确类型、false和0保留、取消不写、字段中文错误定位且保留其他输入、409不覆盖、新增超时重试同UUID；同名关联选项两ID可分别选择；快慢两次搜索结果只能采用最新查询，`expect(screen.queryByText("旧查询结果")).toBeNull()`；history 显示旧字段名和旧新值但 `expect(screen.queryByRole("button",{name:"保存"})).toBeNull()`。
- [ ] 运行 `pnpm test -- src/components/settings/data-tables/record-editor.test.tsx src/components/settings/data-tables/records-panel.test.tsx src/components/settings/data-tables/linked-record-picker.test.tsx src/components/settings/data-tables/history-panel.test.tsx` 确认缺行为失败。
- [ ] 实现动态记录表格和表单、分页和受控筛选/排序、关联搜索、记录归档恢复、结构变更后保留草稿并提示刷新；用请求序号忽略旧搜索结果，不能只依赖现有 fetchWithAuth 的 signal。关联标签用真实目标名加记录编号，外链打开目标详情不递归渲染。
- [ ] 同命令 PASS，核验只读字段、空状态、字段越多横向滚动、完整中文问题、归档关系展示及失败恢复、关闭表单未保存提醒；`pnpm exec tsc --noEmit` PASS。
- [ ] 提交 `feat: edit linked custom records and inspect history`。

## Task 11 完整路径和技术债务复核

**Files:** 新增 test_v2/e2e/test_custom_data_tables.py、VERIFICATION.md；完善本轮组件和服务，清理仅新增模块的重复逻辑。
**Interfaces:** 只从公开 API 走流程，expected 手工定义，不能由被测 normalize/query 算出标准答案；浏览器本地使用合成测试账号和表，不向生产写入。

- [ ] 写完整路径测试两张表与八类型，建立关联，改字段显示名，编辑记录，检查旧新历史，非法值拒绝，先解除启用引用再归档恢复，重新登录后 `assert fetched["id"] == original_id`、`assert fetched["values"][number_key] == "2.5"`；同编码产品价格和原PO结果 `assert existing_business_after == existing_business_before`。
- [ ] 运行 `.venv/bin/python -m pytest test_v2/e2e/test_custom_data_tables.py test_v2/unit/dynamic_data test_v2/integration/dynamic_data -q`；真实 PostgreSQL 环境仍须运行 Task 7，不用其中 skip 的默认结果替代。
- [ ] 本地浏览器实际走设置入口、创建两表、配置字段、输入、关联选择、保存、历史、失败修正、分页和新会话读取；保留截图与操作结果到本轮 evidence，明确自动化/人工边界。源码自审所有提交，不只审最后 diff；核对单权威校验、跨领域边界、错误编码、无用helper、历史只读和无新产品字段。
- [ ] 候选稳定后一次 `.venv/bin/python -m pytest -q`；前端 `pnpm test`、`pnpm exec tsc --noEmit`、`pnpm build`；后端 `scripts/check_arch.py` 及触及文件 Ruff。明确已有失败和本轮失败，不通过改预期、宽泛 skip 或删除旧断言制造全绿。
- [ ] 提交 `test: verify custom data table user workflow`，更新 PROGRESS，交付本地入口、测试结果和未上线状态。若没有发布授权，到此停止，不默认推送或部署。

## Task 12 发布兼容和受控交付

**Files:** 修改 infrastructure/config.py、apps/http/startup.py、apps/http/data_tables.py；新增 test_v2/section_1_security/test_data_tables_release.py；本轮发布说明和核验记录。生产合并、推送和部署须阶段授权。
**Interfaces:** settings 新增 `CUSTOM_DATA_TABLES_ENABLED:bool=False` 和 `SCHEMA_RELEASE_TRANSITION:Literal["","custom_data_tables_0034_0035"]=""`。正常 verify_schema 仍精确比较 heads；仅显式 transition、模块 disabled、脚本head=0035 时可接受数据库0034或0035，其余 head 全拒绝。模块 disabled 时全部新模块路由返回503与中文说明；最终启用模块并清空transition。前端收到503不展示可保存的半完成页面。

- [ ] 写启动矩阵测试 old/old、new/new、new/old桥接、new/任意未来head、桥接但模块enabled；只有指定合法组合通过。写新路由关闭时 `assert response.status_code == 503`、原产品接口 `assert products_response.status_code == 200`、错误flag配置拒绝；由 pytest stub 的 MigrationContext 和真实PG演练分别证明逻辑与接通。
- [ ] 运行 `.venv/bin/python -m pytest test_v2/section_1_security/test_data_tables_release.py test_v2/section_1_security/test_release_maintenance.py -q`，先失败再实现；Task 11 验证前该代码必须已落地，不能在完整回归后添加共享启动改动不重验。
- [ ] 实现明确过渡开关和新API服务门禁，测试fixture显式enabled，生产默认disabled；构建兼容桥接和最终同源码不同配置的发布制品。后台扫描入口按实际启动机制核对是否使用head检查，不假称所有Job都经过HTTP lifespan；所有任务镜像仍一致。
- [ ] 获发布授权后先核对Git、Cloud Run/Vercel/DB/Job；备份成功，部署模块disabled桥接并验证旧业务，执行仅增六表迁移，验约束，启用最终后端并清空transition、更新Job、前端部署；健康/鉴权/CORS/设置入口/历史读取/正常调度只读核验。要生产写入测试表时另确认范围，不使用真实产品或订单代替测试记录。
- [ ] 更新 `DEPLOYMENT_VERIFIED_<实际日期>_CUSTOM_DATA_TABLES.md`、本轮PROGRESS和根PROGRESS：代码完成、已上线、用户待验收分开；回退到模块disabled桥接，保留新表与用户资料，不执行生产downgrade。纯文档不重复完整CI。

## 执行顺序与门禁

开发顺序为 Task 1–10，随后先完成 Task 12 的本地代码与聚焦测试，再执行 Task 11 稳定候选验证，最后在授权后执行 Task 12 的生产步骤。该顺序确保共享启动修改包含在最终完整回归里。

实施前在独立工作树创建 `feature/custom-data-tables-20261006`，再次阅读项目部署记录、EXPERIENCE、设计和本计划。不把本地 ahead 的纯文档未经核对一起推送。逐任务提交使用精确路径，不 git add 全工作区；每任务记录完成证据并开展自审，未通过不进入依赖该任务的步骤。

计划自审覆盖表创建与编辑、八类型、唯一值、关联、字段排序和状态、记录新增与修改和状态、服务端筛选、历史、角色、重试、并发、页面和发布。第一版“不包含”功能均无实现任务；本轮不等待或修复其他 PO 缺陷来冒充范围完成。

当前仅设计及计划完成，所有执行 checkbox 未勾选。用户审阅计划并选择主代理原生执行或分工执行后，再进入实现。

2026-10-06 官方 npm 元数据核对：本机 Node v22.20.0，CI Node 22；最新 jsdom 30.1.2 要求至少 Node 22.22.2，因此选用 engines>=18 的 [jsdom 26.1.0](https://registry.npmjs.org/jsdom/26.1.0)。[Testing Library React 16.3.3](https://registry.npmjs.org/@testing-library/react/16.3.3) 支持 React 19，并需 [DOM 10.4.2](https://registry.npmjs.org/@testing-library/dom/10.4.2)；[user-event 14.6.7](https://registry.npmjs.org/@testing-library/user-event/14.6.7) 与上述 DOM 主版本兼容。此核对只读包元数据，没有安装依赖；测试安装和实际运行仍是 Task 9 验收。
