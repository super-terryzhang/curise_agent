# 自定义数据表受控发布与回退

状态：仅本地代码及演练，本轮没有生产迁移或部署。迁移为 0034_drop_product_validity → 0035_custom_data_tables，只增六个独立表。

1. 获得发布授权后核对 main、运行镜像 digest、数据库 head、Vercel 和 Oracle Job；本地验收不能代替生产核对。完成数据库备份后才能运行迁移。
2. 同一源码先发布过渡配置：CUSTOM_DATA_TABLES_ENABLED=false，SCHEMA_RELEASE_TRANSITION=custom_data_tables_0034_0035。后端和 scripts/scan_oracle_pos.py 的 verify_schema 只接受 0034/0035，不接受其他 head；健康、鉴权、旧产品及扫描需核验。
3. 在专用发布步骤执行 alembic upgrade 0035_custom_data_tables；验证六表、UUID/JSONB、复合外键、唯一约束、旧表/旧行保持。不要用应用启动隐式迁移。
4. 发布最终配置：CUSTOM_DATA_TABLES_ENABLED=true，SCHEMA_RELEASE_TRANSITION 留空，恢复精确 head。后端和 Oracle Job 同镜像，最后发布前端；生产只读核验入口、表/历史读取、角色、旧业务与扫描调度。
5. 记录源码、CI、镜像 digest、revision、前端 deployment、Job generation 和数据库 head。生产写入验收需明确授权的测试表；无授权则仅只读冒烟与用户自测。

回退：关闭新模块并回到指定过渡配置，保留六表及用户资料。禁止自动 downgrade 或物理删除表。下一迁移不得继续复用此桥接名称；不把允许旧head永久留在最终服务。

本地演练：独立 PostgreSQL17，127.0.0.1:55447/cruise_data_tables_test；测试每项使用随机 owned schema，未配置环境变量显式 skip，必须另报未验证。CI 指向既有 127.0.0.1:5432/cruise_migration_test，同样 schema 隔离。
