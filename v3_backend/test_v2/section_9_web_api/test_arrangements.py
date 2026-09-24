from domains.inquiry.models import Inquiry, InquirySupplier
from domains.masterdata.models import Country, Port, Supplier
from domains.orders.groups import service
from domains.orders.groups.automation import regroup
from domains.orders.models import Order
from test_v2.fixtures.helpers import login, seed_user
from test_v2.section_9_web_api.test_auto_groups import order


def test_summary_scopes_members_and_separates_generation_from_fulfillment(client, db):
    a = seed_user(db, email='a@test')
    b = seed_user(db, email='b@test')
    seed_user(db, email='admin@test', role='admin')
    x = order(db, a.id, po_number='VISIBLE')
    x.product_count = 7
    hidden = order(db, b.id, po_number='SECRET')
    bad = order(db, a.id, loading_date=None)
    db.add(Inquiry(order_id=x.id, status='completed')); db.commit()
    regroup(db, apply=True)
    h = login(client, 'a@test')
    r = client.get('/api/order-groups/arrangements', headers=h)
    assert r.status_code == 200
    data = r.json()
    assert 'SECRET' not in r.text
    assert data['total_orders'] == 2
    g = data['arrangements'][0]
    assert g['can_manage'] is False and g['can_generate_inquiry'] is False
    assert [o['id'] for o in g['orders']] == [x.id]
    assert g['orders'][0]['inquiry_status'] == 'completed'
    assert g['orders'][0]['fulfillment_status'] == 'pending'
    assert g['orders'][0]['product_count'] == 7
    assert data['unclassified'][0]['id'] == bad.id
    assert data['unclassified'][0]['reason'] == '缺少或无法识别装船日'
    assert client.post(f'/api/order-groups/orders/{hidden.id}/classify', headers=h).status_code == 404
    admin = client.get('/api/order-groups/arrangements', headers=login(client, 'admin@test')).json()
    assert admin['total_orders'] == 3
    assert len(admin['arrangements'][0]['orders']) == 2


def test_unclassified_manual_choice_retry_and_zero_bucket(client, db):
    u = seed_user(db, email='a@test')
    x = order(db, u.id)
    regroup(db, apply=True)
    h = login(client, 'a@test')
    data = client.get('/api/order-groups/arrangements', headers=h).json()
    assert data['unclassified'] == [] and len(data['arrangements']) == 1
    service.remove_order(db, group_id=x.group_id, user_id=u.id, order_id=x.id)
    regroup(db, apply=True)
    data = client.get('/api/order-groups/arrangements', headers=h).json()
    assert data['arrangements'] == []
    assert data['unclassified'][0]['reason'] == '已手动移至未分类'
    r = client.post(f'/api/order-groups/orders/{x.id}/classify', headers=h)
    assert r.status_code == 200 and r.json()['group_id'] is not None
    x.loading_date = None; db.commit()
    r = client.post(f'/api/order-groups/orders/{x.id}/classify', headers=h)
    assert r.json() == {'group_id': None, 'reason': '缺少或无法识别装船日'}


def test_all_members_returned_without_order_page_limit(client, db):
    u = seed_user(db, email='a@test')
    port=Port(name='東京'); db.add(port); db.flush()
    for i in range(105):
        db.add(Order(user_id=u.id, filename=f'{i}.pdf', ship_name='SOLSTICE', loading_date='2026-09-11', destination_port='TOKYO', port_id=port.id, status='ready'))
    db.commit(); regroup(db, apply=True)
    r = client.get('/api/order-groups/arrangements', headers=login(client, 'a@test')).json()
    assert r['total_orders'] == 105 and len(r['arrangements']) == 1
    assert len(r['arrangements'][0]['orders']) == 105


def test_delivery_date_does_not_fill_missing_loading_date(client, db):
    u = seed_user(db, email='a@test')
    order(db, u.id, loading_date=None, delivery_date='28-Mar-2026', port_id=None, destination_port='TOKYO (YOKOHAMA)')
    r = client.get('/api/order-groups/arrangements', headers=login(client, 'a@test')).json()
    assert r['unclassified'][0]['day'] is None
    assert r['unclassified'][0]['reason'] == '缺少或无法识别装船日；请选择目标港口'


def test_selected_port_is_the_only_api_destination_and_can_be_cleared(client, db):
    u = seed_user(db, email='a@test')
    x = order(db, u.id, destination_port='WRONG EXTRACTION', order_metadata={'destination_port':'ANOTHER PORT'})
    regroup(db, apply=True)
    h=login(client,'a@test')
    detail=client.get(f'/api/orders/{x.id}',headers=h).json()
    assert detail['order_metadata']['destination_port']=='東京'
    listing=client.get('/api/orders',headers=h).json()
    assert listing['items'][0]['order_metadata']['destination_port']=='東京'
    result=client.patch(f'/api/orders/{x.id}',headers=h,json={'port_id':None})
    assert result.status_code==200
    assert result.json()['port_id'] is None and result.json()['order_metadata']['destination_port'] is None
    summary=client.get('/api/order-groups/arrangements',headers=h).json()
    assert summary['unclassified'][0]['port'] is None
    assert summary['unclassified'][0]['reason']=='请选择目标港口'


def test_background_match_classifies_after_selecting_port(db,monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from domains.orders import service as orders_service
    u=seed_user(db,email='a@test')
    x=order(db,u.id,port_id=None)
    port=db.query(Port).filter_by(name='東京').one()
    monkeypatch.setattr('infrastructure.db.session.SessionLocal',lambda: nullcontext(db))

    def select_port(order, session):
        order.port_id = port.id
        return {
            "statistics": {
                "total": 0,
                "matched": 0,
                "not_matched": 0,
                "match_rate": 0.0,
            }
        }

    monkeypatch.setattr('domains.orders.automation.run_matching', select_port)
    monkeypatch.setattr(
        'domains.orders.automation.queue_inquiry_for_group',
        lambda session, group_id: SimpleNamespace(id=1),
    )
    monkeypatch.setattr(
        'domains.orders.automation.run_inquiry_for_group',
        lambda group_id, **kwargs: SimpleNamespace(
            id=1,
            version=1,
            status='completed',
            supplier_count=0,
            unassigned_count=0,
            model_dump=lambda mode: {"unmatched_items": [], "suppliers": []},
        ),
    )
    orders_service._run_matching_for_order_sync(x.id)
    assert x.status=='ready' and x.group_id is not None


def test_arrangement_summary_surfaces_orders_requiring_human_review(client, db):
    user = seed_user(db, email='review@test')
    item = order(db, user.id)
    item.anomaly_data = {
        'requires_human_review': True,
        'error_count': 2,
        'blocking_count': 1,
    }
    db.commit()
    regroup(db, apply=True)

    data = client.get(
        '/api/order-groups/arrangements',
        headers=login(client, 'review@test'),
    ).json()

    summary = data['arrangements'][0]['orders'][0]
    assert summary['requires_human_review'] is True
    assert summary['anomaly_count'] == 3


def test_arrangement_counts_current_issues_without_historical_inquiry_exclusions(client, db):
    user = seed_user(db, email='actionable-rows@test')
    existing = order(db, user.id, po_number='PO-EXISTING', product_count=2)
    latest = order(
        db,
        user.id,
        po_number='PO-LATEST',
        product_count=1,
        match_statistics={'total': 1, 'matched': 0, 'not_matched': 1},
        match_results=[{
            'source_order_id': None,
            'source_line_id': 'latest-1',
            'product_name': 'RED BULL',
            'match_status': 'not_matched',
        }],
    )
    db.flush()
    latest.match_results[0]['source_order_id'] = latest.id
    latest.anomaly_data = {
        'requires_human_review': True,
        'error_count': 5,
        'blocking_count': 0,
        'findings': [
            {
                'code': 'RFQ_ROW_EXCLUDED', 'severity': 'error', 'scope': 'row',
                'source_order_id': existing.id, 'line_id': 'existing-1',
            },
            {
                'code': 'RFQ_ROW_EXCLUDED', 'severity': 'error', 'scope': 'row',
                'source_order_id': existing.id, 'line_id': 'existing-2',
            },
            {
                'code': 'PRODUCT_NOT_MATCHED', 'severity': 'error', 'scope': 'row',
                'source_order_id': latest.id, 'line_id': 'latest-1',
            },
            {
                'code': 'EXACT_UNIQUE_MATCH_REQUIRED', 'severity': 'error', 'scope': 'row',
                'line_id': 'latest-1',
            },
            {
                'code': 'RFQ_ROW_EXCLUDED', 'severity': 'error', 'scope': 'row',
                'source_order_id': latest.id, 'line_id': 'latest-1',
            },
        ],
    }
    db.commit()
    regroup(db, apply=True)

    headers = login(client, 'actionable-rows@test')
    listing = client.get('/api/order-groups/arrangements', headers=headers).json()
    arrangement = listing['arrangements'][0]
    counts = {item['po_number']: item['anomaly_count'] for item in arrangement['orders']}

    assert counts == {'PO-EXISTING': 0, 'PO-LATEST': 1}
    summaries = {item['po_number']: item for item in arrangement['orders']}
    assert summaries['PO-EXISTING']['requires_human_review'] is False
    assert summaries['PO-LATEST']['requires_human_review'] is True

    workspace = client.get(
        f"/api/order-groups/arrangements/{arrangement['id']}", headers=headers
    ).json()
    assert workspace['summary']['anomaly_count'] == 1

    detail = client.get(f'/api/orders/{latest.id}', headers=headers).json()
    assert detail['actionable_count'] == 1
    existing_detail = client.get(f'/api/orders/{existing.id}', headers=headers).json()
    assert existing_detail['actionable_count'] == 0


def test_removed_po_marks_latest_inquiry_membership_as_changed(client, db):
    user = seed_user(db, email='membership@test')
    first = order(db, user.id, po_number='PO-1')
    second = order(db, user.id, po_number='PO-2')
    regroup(db, apply=True)
    assert first.group_id == second.group_id
    group_id = first.group_id
    db.add(Inquiry(
        order_id=first.id,
        group_id=group_id,
        version=1,
        status='completed',
        member_snapshot=[{'order_id': first.id}, {'order_id': second.id}],
    ))
    db.commit()

    service.remove_order(
        db, group_id=group_id, user_id=user.id, order_id=second.id
    )
    data = client.get(
        '/api/order-groups/arrangements', headers=login(client, 'membership@test')
    ).json()

    arrangement = data['arrangements'][0]
    assert arrangement['inquiry_version'] == 1
    assert arrangement['inquiry_members_changed'] is True
    assert arrangement['inquiry_member_diff'] == {
        'added_order_ids': [],
        'removed_order_ids': [second.id],
    }


def test_workspace_aggregates_visible_po_supplier_and_activity(client, db):
    user = seed_user(db, email='workspace@test')
    supplier = Supplier(name='Yokohama Marine Foods')
    db.add(supplier)
    db.flush()
    first = order(
        db,
        user.id,
        po_number='PO-A',
        product_count=2,
        products=[{'product_name': 'A'}, {'product_name': 'B'}],
        match_statistics={'total': 2, 'matched': 2, 'not_matched': 0},
        match_results=[
            {'product_name': 'A', 'match_status': 'matched', 'matched_product': {'supplier_id': supplier.id}},
            {'product_name': 'B', 'match_status': 'matched', 'matched_product': {'supplier_id': supplier.id}},
        ],
        anomaly_data={'requires_human_review': False, 'total_anomalies': 0},
    )
    second = order(
        db,
        user.id,
        po_number='PO-B',
        product_count=1,
        products=[{'product_name': 'Missing'}],
        match_statistics={'total': 1, 'matched': 0, 'not_matched': 1},
        match_results=[{'product_name': 'Missing', 'match_status': 'not_matched', 'match_reason': '未找到匹配商品'}],
        anomaly_data={'requires_human_review': True, 'error_count': 1},
    )
    regroup(db, apply=True)
    inquiry = Inquiry(
        order_id=first.id,
        group_id=first.group_id,
        version=1,
        status='partial',
        supplier_count=1,
        unassigned_count=1,
        match_snapshot=[
            {'source_order_id': first.id, 'source_po_number': 'PO-A', 'product_name': 'A', 'match_status': 'matched', 'matched_product': {'supplier_id': supplier.id}},
            {'source_order_id': first.id, 'source_po_number': 'PO-A', 'product_name': 'B', 'match_status': 'matched', 'matched_product': {'supplier_id': supplier.id}},
            {'source_order_id': second.id, 'source_po_number': 'PO-B', 'product_name': 'Missing', 'match_status': 'not_matched', 'match_reason': '未找到匹配商品'},
        ],
    )
    db.add(inquiry)
    db.flush()
    db.add(InquirySupplier(
        inquiry_id=inquiry.id,
        supplier_id=supplier.id,
        supplier_name=supplier.name,
        product_count=2,
        status='completed',
    ))
    db.commit()

    response = client.get(
        f'/api/order-groups/arrangements/{first.group_id}',
        headers=login(client, 'workspace@test'),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data['summary']['product_count'] == 3
    assert data['summary']['matched_count'] == 2
    assert data['summary']['unmatched_count'] == 1
    assert data['summary']['supplier_count'] == 1
    assert data['summary']['anomaly_count'] == 1
    assert [item['po_number'] for item in data['arrangement']['orders']] == ['PO-A', 'PO-B']
    assert data['suppliers'] == [{
        'supplier_id': supplier.id,
        'supplier_name': 'Yokohama Marine Foods',
        'product_count': 2,
        'source_order_count': 1,
        'source_po_numbers': ['PO-A'],
        'status': 'completed',
        'error_message': None,
        'template_id': None,
        'template_name': None,
        'template_method': 'unavailable',
    }]
    assert data['unassigned_items'][0]['source_po_number'] == 'PO-B'
    assert data['latest_inquiry']['version'] == 1
    assert any(item['kind'] == 'po_received' for item in data['activity'])


def test_workspace_does_not_leak_other_owners_po_or_supplier(client, db):
    alice = seed_user(db, email='workspace-a@test')
    bob = seed_user(db, email='workspace-b@test')
    visible_supplier = Supplier(name='Visible Supplier')
    hidden_supplier = Supplier(name='Hidden Supplier')
    db.add_all([visible_supplier, hidden_supplier])
    db.flush()
    visible = order(
        db,
        alice.id,
        po_number='VISIBLE-PO',
        product_count=1,
        match_statistics={'total': 1, 'matched': 1, 'not_matched': 0},
    )
    hidden = order(
        db,
        bob.id,
        po_number='SECRET-PO',
        product_count=1,
        match_statistics={'total': 1, 'matched': 1, 'not_matched': 0},
    )
    regroup(db, apply=True)
    inquiry = Inquiry(
        order_id=visible.id,
        group_id=visible.group_id,
        version=1,
        status='completed',
        match_snapshot=[
            {'source_order_id': visible.id, 'source_po_number': 'VISIBLE-PO', 'match_status': 'matched', 'matched_product': {'supplier_id': visible_supplier.id}},
            {'source_order_id': hidden.id, 'source_po_number': 'SECRET-PO', 'match_status': 'matched', 'matched_product': {'supplier_id': hidden_supplier.id}},
        ],
    )
    db.add(inquiry)
    db.flush()
    db.add_all([
        InquirySupplier(inquiry_id=inquiry.id, supplier_id=visible_supplier.id, supplier_name=visible_supplier.name, product_count=1, status='completed'),
        InquirySupplier(inquiry_id=inquiry.id, supplier_id=hidden_supplier.id, supplier_name=hidden_supplier.name, product_count=1, status='completed'),
    ])
    db.commit()

    response = client.get(
        f'/api/order-groups/arrangements/{visible.group_id}',
        headers=login(client, 'workspace-a@test'),
    )

    assert response.status_code == 200, response.text
    assert 'SECRET-PO' not in response.text
    assert 'Hidden Supplier' not in response.text
    data = response.json()
    assert data['summary']['product_count'] == 1
    assert [item['supplier_name'] for item in data['suppliers']] == ['Visible Supplier']
    assert [item['po_number'] for item in data['arrangement']['orders']] == ['VISIBLE-PO']


def test_arrangement_edit_updates_every_po_and_rematches_shared_scope(client, db):
    user = seed_user(db, email='arrangement-edit@test')
    first = order(db, user.id, po_number='PO-EDIT-1')
    second = order(db, user.id, po_number='PO-EDIT-2')
    regroup(db, apply=True)
    assert first.group_id == second.group_id
    country = Country(name='Japan', code='JPN')
    db.add(country)
    db.flush()
    port = Port(name='横浜', code='JPYOK', country_id=country.id)
    db.add(port)
    db.commit()

    response = client.patch(
        f'/api/order-groups/arrangements/{first.group_id}',
        headers=login(client, 'arrangement-edit@test'),
        json={
            'ship_name': 'DIAMOND PRINCESS',
            'loading_date': '2026-10-08',
            'port_id': port.id,
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data['arrangement']['ship'] == 'DIAMOND PRINCESS'
    assert data['arrangement']['day'] == '2026-10-08'
    assert data['arrangement']['port_id'] == port.id
    assert data['arrangement']['port'] == '横浜'
    db.expire_all()
    members = db.query(Order).filter(Order.group_id == first.group_id).all()
    assert len(members) == 2
    assert all(item.ship_name == 'DIAMOND PRINCESS' for item in members)
    assert all(item.loading_date == '2026-10-08' for item in members)
    assert all(item.port_id == port.id and item.country_id == country.id for item in members)
    assert all(item.order_metadata['destination_port'] == '横浜' for item in members)
