"""Section 7 — Settings: CRUD for field schemas / templates / locations / company config.

测试目标：
    `domains/settings/service.py` 里所有 CRUD 函数：
      - 写进去后从 DB 真的能 list 回来 (端到端通)
      - update_xxx(body, exclude_unset) 不会把没传的字段清空
      - 删除核心字段 (is_core=True) 必须被拒
      - seed_default_field_schema 幂等 — 再调一次不会出第二份
      - company_config 是 upsert: 一次只更新一个 key，其它 key 不掉

为什么重要：
    这一层是后台管理员的"设置面板"的全部业务依赖。一旦它 bug:
      - 删除核心字段 → 后续订单提取直接散架
      - upsert 不是真正 upsert → 改一项把另一项清成空
      - 默认模式重复 seed → 数据库长一堆同名重复行

设计方法：
    不走 HTTP，直接调 service 函数 — 这才是稳定的业务边界。
    每个 test 用一条 fresh 数据 + commit + re-read 验证持久化，
    而不是相信 service 返回的实例属性。
"""

from __future__ import annotations

import pytest

from domains.settings import service as svc
from domains.settings.errors import BadRequest, NotFound
from domains.settings.models import (
    CompanyConfig,
    DeliveryLocation,
    FieldDefinition,
    FieldSchema,
    OrderFormatTemplate,
)
from domains.settings.schemas import (
    CompanyConfigItem,
    CompanyConfigUpdate,
    DeliveryLocationCreate,
    DeliveryLocationUpdate,
    FieldDefinitionCreate,
    FieldDefinitionUpdate,
    FieldSchemaCreate,
    OrderFormatTemplateCreate,
    OrderFormatTemplateUpdate,
)

CREATOR_ID = 1


# ─── Field Schema CRUD ────────────────────────────────────────


def test_create_field_schema_then_list_returns_it(db) -> None:
    """写进去 + list 出来 — 验证一条最简单的 round-trip。"""
    created = svc.create_field_schema(
        db, FieldSchemaCreate(name="标准 PO", description="主用"), created_by=CREATOR_ID
    )
    assert created.id is not None

    rows = svc.list_field_schemas(db)
    assert any(r.id == created.id and r.name == "标准 PO" for r in rows)


def test_update_field_schema_changes_name_and_description(db) -> None:
    s = svc.create_field_schema(
        db, FieldSchemaCreate(name="旧名", description="旧描述"), created_by=CREATOR_ID
    )
    updated = svc.update_field_schema(
        db, s.id, FieldSchemaCreate(name="新名", description="新描述")
    )
    assert updated.name == "新名"

    # Re-read for真正落盘验证
    reread = db.get(FieldSchema, s.id)
    assert reread.name == "新名"
    assert reread.description == "新描述"


def test_delete_field_schema_removes_row(db) -> None:
    s = svc.create_field_schema(
        db, FieldSchemaCreate(name="待删除"), created_by=CREATOR_ID
    )
    svc.delete_field_schema(db, s.id)
    assert db.get(FieldSchema, s.id) is None


def test_get_field_schema_missing_raises_not_found(db) -> None:
    """不存在的 id → NotFound — service 层负责把数据库 None 翻译成 domain error。"""
    with pytest.raises(NotFound):
        svc.get_field_schema(db, 99999)


# ─── Default schema seed: idempotency ─────────────────────────


def test_seed_default_field_schema_is_idempotent(db) -> None:
    """重复 seed 不能出多份: 是 "如果不存在就建" 的 upsert 语义。"""
    first = svc.seed_default_field_schema(db, created_by=CREATOR_ID)
    second = svc.seed_default_field_schema(db, created_by=CREATOR_ID)

    assert first.id == second.id

    # DB 里就这一行 is_default=True 的
    defaults = (
        db.query(FieldSchema).filter(FieldSchema.is_default.is_(True)).all()
    )
    assert len(defaults) == 1

    # 8 个核心字段也只生成了一次
    defs = (
        db.query(FieldDefinition).filter(FieldDefinition.schema_id == first.id).all()
    )
    assert len(defs) == 8


# ─── Field Definitions nested under a schema ──────────────────


def test_add_field_definition_attaches_to_schema(db) -> None:
    s = svc.create_field_schema(
        db, FieldSchemaCreate(name="自定义"), created_by=CREATOR_ID
    )
    body = FieldDefinitionCreate(
        field_key="custom_remark",
        field_label="自由备注",
        field_type="string",
        is_core=False,
        sort_order=10,
    )
    defn = svc.add_field_definition(db, s.id, body)
    assert defn.schema_id == s.id
    assert defn.field_key == "custom_remark"


def test_update_field_definition_only_changes_provided_fields(db) -> None:
    """exclude_unset 必须生效 — 没传的字段不应被设成 None / default。"""
    s = svc.seed_default_field_schema(db, created_by=CREATOR_ID)
    # product_name 是第一个 sort_order=1 的核心字段
    pn = next(d for d in s.definitions if d.field_key == "product_name")
    assert pn.is_required is True  # 起始状态

    svc.update_field_definition(
        db, s.id, pn.id, FieldDefinitionUpdate(field_label="商品名称")
    )
    db.refresh(pn)
    assert pn.field_label == "商品名称"
    # 这些字段我们没传，必须保留
    assert pn.is_required is True
    assert pn.field_type == "string"


def test_cannot_delete_core_field_definition(db) -> None:
    """is_core=True 的字段不可删 — 删了会让后续订单提取无法正确解析。"""
    s = svc.seed_default_field_schema(db, created_by=CREATOR_ID)
    core = next(d for d in s.definitions if d.is_core)
    with pytest.raises(BadRequest):
        svc.delete_field_definition(db, s.id, core.id)
    # 行还在
    assert db.get(FieldDefinition, core.id) is not None


def test_can_delete_non_core_field_definition(db) -> None:
    """非核心字段是可删的 — 对照组，确认守门只挡 is_core。"""
    s = svc.create_field_schema(
        db, FieldSchemaCreate(name="自定义"), created_by=CREATOR_ID
    )
    defn = svc.add_field_definition(
        db,
        s.id,
        FieldDefinitionCreate(
            field_key="weather", field_label="天气", field_type="string", is_core=False
        ),
    )
    svc.delete_field_definition(db, s.id, defn.id)
    assert db.get(FieldDefinition, defn.id) is None


# ─── Order Format Template CRUD ───────────────────────────────


def test_create_and_list_order_template(db) -> None:
    body = OrderFormatTemplateCreate(
        name="RC Standard",
        file_type="excel",
        source_company="Royal Caribbean",
        header_row=4,
        data_start_row=5,
    )
    created = svc.create_order_template(db, body, created_by=CREATOR_ID)
    rows = svc.list_order_templates(db)
    assert any(r.id == created.id and r.name == "RC Standard" for r in rows)


def test_update_order_template_partial_keeps_other_fields(db) -> None:
    body = OrderFormatTemplateCreate(
        name="原始",
        file_type="pdf",
        source_company="MSC Cruises",
        notes="原注释",
    )
    tpl = svc.create_order_template(db, body, created_by=CREATOR_ID)
    svc.update_order_template(
        db, tpl.id, OrderFormatTemplateUpdate(name="重命名")
    )
    db.refresh(tpl)
    assert tpl.name == "重命名"
    assert tpl.source_company == "MSC Cruises"  # 没动
    assert tpl.notes == "原注释"  # 没动


def test_delete_order_template_removes_row(db) -> None:
    tpl = svc.create_order_template(
        db, OrderFormatTemplateCreate(name="一次性"), created_by=CREATOR_ID
    )
    svc.delete_order_template(db, tpl.id)
    assert db.get(OrderFormatTemplate, tpl.id) is None


def test_get_order_template_missing_raises(db) -> None:
    with pytest.raises(NotFound):
        svc.get_order_template(db, 99999)


# ─── Delivery Locations CRUD ──────────────────────────────────


def test_create_and_list_delivery_locations(db) -> None:
    a = svc.create_delivery_location(
        db,
        DeliveryLocationCreate(name="码头 A", address="A 路 1 号"),
        created_by=CREATOR_ID,
    )
    b = svc.create_delivery_location(
        db,
        DeliveryLocationCreate(name="码头 B"),
        created_by=CREATOR_ID,
    )
    rows = svc.list_delivery_locations(db)
    ids = {r.id for r in rows}
    assert {a.id, b.id}.issubset(ids)


def test_update_delivery_location_partial(db) -> None:
    loc = svc.create_delivery_location(
        db,
        DeliveryLocationCreate(name="码头", address="原地址", contact_person="原人"),
        created_by=CREATOR_ID,
    )
    svc.update_delivery_location(
        db, loc.id, DeliveryLocationUpdate(address="新地址")
    )
    db.refresh(loc)
    assert loc.address == "新地址"
    assert loc.contact_person == "原人"  # 没传 → 保留


def test_delete_delivery_location_removes_row(db) -> None:
    loc = svc.create_delivery_location(
        db, DeliveryLocationCreate(name="码头"), created_by=CREATOR_ID
    )
    svc.delete_delivery_location(db, loc.id)
    assert db.get(DeliveryLocation, loc.id) is None


def test_update_missing_delivery_location_raises(db) -> None:
    with pytest.raises(NotFound):
        svc.update_delivery_location(
            db, 99999, DeliveryLocationUpdate(name="不存在")
        )


# ─── Company Config: upsert semantics ─────────────────────────


def test_update_company_config_creates_then_updates(db) -> None:
    """第一次 update_company_config 会 insert，第二次相同 key 会 update。"""
    svc.update_company_config(
        db,
        CompanyConfigUpdate(items=[CompanyConfigItem(key="company_name", value="老 K")]),
        updated_by=CREATOR_ID,
    )
    row = db.query(CompanyConfig).filter(CompanyConfig.key == "company_name").one()
    assert row.value == "老 K"

    svc.update_company_config(
        db,
        CompanyConfigUpdate(items=[CompanyConfigItem(key="company_name", value="老 J")]),
        updated_by=CREATOR_ID,
    )
    # 还是同一行，但 value 换了
    rows = (
        db.query(CompanyConfig).filter(CompanyConfig.key == "company_name").all()
    )
    assert len(rows) == 1
    assert rows[0].value == "老 J"


def test_update_company_config_one_key_does_not_clobber_others(db) -> None:
    """改 key A 不能让 key B 消失 — upsert 必须是 *逐 key* 处理。"""
    svc.update_company_config(
        db,
        CompanyConfigUpdate(
            items=[
                CompanyConfigItem(key="company_name", value="老 K"),
                CompanyConfigItem(key="vat_no", value="VAT-001"),
            ]
        ),
        updated_by=CREATOR_ID,
    )
    # 只改 company_name
    svc.update_company_config(
        db,
        CompanyConfigUpdate(items=[CompanyConfigItem(key="company_name", value="老 J")]),
        updated_by=CREATOR_ID,
    )
    vat = db.query(CompanyConfig).filter(CompanyConfig.key == "vat_no").one()
    assert vat.value == "VAT-001"


# ─── List stability ───────────────────────────────────────────


def test_list_field_schemas_returns_in_id_order(db) -> None:
    """list_field_schemas 是按 id ASC 返回 — 这样前端不会因 join 顺序闪烁。"""
    s1 = svc.create_field_schema(db, FieldSchemaCreate(name="A"), created_by=CREATOR_ID)
    s2 = svc.create_field_schema(db, FieldSchemaCreate(name="B"), created_by=CREATOR_ID)
    s3 = svc.create_field_schema(db, FieldSchemaCreate(name="C"), created_by=CREATOR_ID)
    rows = svc.list_field_schemas(db)
    ids = [r.id for r in rows]
    assert ids == sorted(ids), "必须 id 升序"
    # 三条都在
    assert {s1.id, s2.id, s3.id}.issubset(set(ids))


def test_list_order_templates_returns_newest_first(db) -> None:
    """订单模板按 id DESC 排 — 最新加的排最上头，匹配前端"最近模板优先"语义。"""
    t1 = svc.create_order_template(
        db, OrderFormatTemplateCreate(name="老"), created_by=CREATOR_ID
    )
    t2 = svc.create_order_template(
        db, OrderFormatTemplateCreate(name="中"), created_by=CREATOR_ID
    )
    t3 = svc.create_order_template(
        db, OrderFormatTemplateCreate(name="新"), created_by=CREATOR_ID
    )
    rows = svc.list_order_templates(db)
    # 仅看我们刚创建的 3 条
    relevant = [r for r in rows if r.id in (t1.id, t2.id, t3.id)]
    assert [r.id for r in relevant] == [t3.id, t2.id, t1.id]
