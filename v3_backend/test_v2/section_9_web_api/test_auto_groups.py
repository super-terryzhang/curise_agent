from domains.orders.groups.automation import regroup, auto_group_order
from domains.orders.groups import service
from domains.orders.groups.rules import grouping_identity
from domains.orders.models import Order, OrderGroup
from domains.masterdata.models import Port
from test_v2.fixtures.helpers import seed_user, login


def order(db, uid, **values):
    port = db.query(Port).filter_by(name='東京').first()
    if port is None:
        port = Port(name='東京'); db.add(port); db.flush()
    values.setdefault('port_id', port.id)
    o = Order(user_id=uid, filename='po.pdf', status='ready',
              **{'ship_name':'MILLENNIUM', 'loading_date':'2026-09-11',
                 'destination_port':'TOKYO', **values})
    db.add(o);db.commit();return o


def test_cross_owner_group_idempotency_and_singletons(db):
    a=seed_user(db,email='a@test');b=seed_user(db,email='b@test')
    x=order(db,a.id);y=order(db,b.id,ship_name=' millennium ')
    other=order(db,a.id,loading_date='2026-09-12')
    first=regroup(db,apply=True)
    assert first['assigned_orders']==3 and first['created_groups']==2
    assert x.group_id==y.group_id and other.group_id not in (None, x.group_id)
    again=regroup(db,apply=True)
    assert again['assigned_orders']==0 and again['created_groups']==0
    assert db.query(OrderGroup).count()==2


def test_selected_port_wins_over_extraction_and_missing_selection_is_explicit(db):
    u=seed_user(db,email='a@test')
    p=Port(name='大阪');db.add(p);db.commit()
    x=order(db,u.id,port_id=p.id,destination_port='TOKYO (YOKOHAMA)')
    y=order(db,u.id,port_id=p.id,destination_port='wrong extraction')
    other=order(db,u.id,destination_port='OSAKA')
    missing=order(db,u.id,port_id=None,destination_port='OSAKA')
    bad_date=order(db,u.id,loading_date='maybe September')
    regroup(db,apply=True)
    assert x.group_id == y.group_id and x.group_id is not None
    assert other.group_id not in (None, x.group_id)
    assert missing.group_id is None and bad_date.group_id is None
    ports={p.id:p}
    assert grouping_identity(x,ports)[0]['port_label']=='大阪'
    assert grouping_identity(missing,ports)[1]=='请选择目标港口'


def test_loading_day_and_port_group_without_considering_ship(db):
    u=seed_user(db,email='a@test')
    x=order(db,u.id,ship_name=None,loading_date='MARCH 08 2026')
    y=order(db,u.id,ship_name='SOLSTICE',loading_date='08-Mar-2026')
    other=order(db,u.id,ship_name='OTHER',loading_date='2026-03-08')
    regroup(db,apply=True)
    assert x.group_id==y.group_id==other.group_id
    assert x.group_id is not None


def test_delivery_day_never_substitutes_for_missing_loading_day(db):
    u=seed_user(db,email='a@test')
    x=order(db,u.id,loading_date=None,delivery_date='2026-03-08')
    result=regroup(db,apply=True)
    assert x.group_id is None
    assert result['skipped']==[{'order_id':x.id,'reason':'缺少或无法识别装船日'}]


def test_edit_moves_automatic_membership_and_manual_removal_sticks(db):
    u=seed_user(db,email='a@test')
    x=order(db,u.id);y=order(db,u.id)
    regroup(db,apply=True);old=x.group_id
    x.loading_date='2026-09-12';db.commit()
    auto_group_order(db,x.id)
    assert x.group_id not in (None, old) and y.group_id==old
    x.loading_date='2026-09-11';db.commit();auto_group_order(db,x.id)
    assert x.group_id==old
    service.remove_order(db,group_id=old,user_id=u.id,order_id=x.id)
    regroup(db,apply=True)
    assert x.group_id is None


def test_shared_group_permissions_and_counts(client,db):
    a=seed_user(db,email='a@test');b=seed_user(db,email='b@test')
    seed_user(db,email='admin@test',role='admin')
    x=order(db,a.id);y=order(db,b.id)
    regroup(db,apply=True);gid=x.group_id
    ah=login(client,'a@test');bh=login(client,'b@test');adminh=login(client,'admin@test')
    for headers in (ah,bh):
        groups=client.get('/api/order-groups',headers=headers).json()
        assert len(groups)==1 and groups[0]['order_count']==1
        assert groups[0]['can_manage'] is False
        assert client.post(f'/api/order-groups/{gid}/generate-inquiry',headers=headers).status_code==400
        assert client.delete(f'/api/order-groups/{gid}',headers=headers).status_code==400
    assert client.get(f'/api/orders/{y.id}',headers=ah).status_code==404
    listing=client.get('/api/orders',headers=adminh).json()
    assert len(listing['items']) == 2
    assert all(o['group_id']==gid and o['loading_date']=='2026-09-11' for o in listing['items'])
    groups=client.get('/api/order-groups',headers=adminh).json()
    assert groups[0]['order_count']==2 and groups[0]['can_manage'] is True
    assert groups[0]['can_generate_inquiry'] is False
    assert client.post(f'/api/order-groups/{gid}/generate-inquiry',headers=adminh).status_code==400
    assert client.post('/api/order-groups/auto-group',headers=ah).status_code==403
    assert client.post('/api/order-groups/auto-group',headers=adminh).status_code==200
    assert client.delete(f'/api/order-groups/{gid}/orders/{x.id}',headers=ah).status_code==204
    assert client.get(f'/api/orders/{y.id}',headers=ah).status_code==404


def test_projection_and_patch_trigger_grouping_and_preserve_manual_choice(client,db,monkeypatch):
    from domains.document.models import Document
    from domains.orders.projection import project_purchase_order
    u=seed_user(db,email='a@test')
    x=order(db,u.id)
    doc=Document(user_id=u.id,filename='second.pdf',doc_type='purchase_order',
        status='extracted', extracted_data={'metadata':{
            'ship_name':'MILLENNIUM','loading_date':'2026-09-11','destination_port':'TOKYO'
        },'products':[{'product_name':'TEST','quantity':1}]})
    db.add(doc);db.commit()
    monkeypatch.setattr('domains.orders.projection.run_matching', lambda order, session: setattr(order, 'port_id', x.port_id))
    y=project_purchase_order(doc,db,run_match_inline=True)
    assert y.group_id==x.group_id and y.group_id is not None
    h=login(client,'a@test')
    r=client.patch(f'/api/orders/{y.id}',headers=h,json={'loading_date':'2026-09-12'})
    assert r.status_code==200 and r.json()['group_id'] not in (None, x.group_id)
    service.remove_order(db,group_id=x.group_id,user_id=u.id,order_id=x.id)
    project_purchase_order(doc,db,run_match_inline=False)
    assert x.group_id is None
