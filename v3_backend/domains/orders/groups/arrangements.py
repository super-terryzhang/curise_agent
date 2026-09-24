"""Permission-scoped supply arrangement read models.

The list view intentionally loads only summary columns.  The workspace detail
view loads matching blobs for one visible arrangement so the frontend can show
PO and supplier readiness without downloading every order in the system.
Reading either view never mutates grouping.
"""
from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from domains.inquiry import (
    Inquiry,
    InquirySupplier,
    SupplierTemplate,
    resolve_supplier_template,
)
from domains.masterdata import Port, Supplier
from domains.orders.anomaly import actionable_row_counts
from domains.orders.groups.automation import MARKER, lock_grouping, mark_manual, regroup
from domains.orders.groups.rules import grouping_identity, identity_key, normalized_date
from domains.orders.groups.schemas import SupplyArrangementUpdate
from domains.orders.groups.service import BadRequest, NotFound, _admin, require_manage
from domains.orders.models import Order, OrderGroup


def list_arrangements(db, *, user_id):
    admin = _admin(db, user_id)
    query = db.query(Order).options(load_only(
        Order.id, Order.user_id, Order.group_id, Order.order_metadata,
        Order.po_number, Order.filename, Order.document_id, Order.ship_name,
        Order.loading_date, Order.delivery_date, Order.destination_port,
        Order.port_id, Order.status, Order.fulfillment_status, Order.anomaly_data,
        Order.product_count,
    ))
    if not admin:
        query = query.filter(Order.user_id == user_id)
    orders = query.order_by(Order.id.desc()).all()
    # Preserve the summary-only query: current records carry row provenance in
    # their findings, while legacy records fall back to stored aggregate counts.
    actionable_counts = actionable_row_counts(orders, include_match_results=False)
    ports = {p.id: p for p in db.query(Port).all()}
    inquiry_rows = db.query(
        Inquiry.id,
        Inquiry.order_id,
        Inquiry.group_id,
        Inquiry.version,
        Inquiry.status,
        Inquiry.member_snapshot,
    ).join(Order, Order.id == Inquiry.order_id).filter(
        True if admin else Order.user_id == user_id
    ).order_by(Inquiry.id.asc()).all()
    order_inquiries = {}
    group_inquiries = {}
    for (
        _inquiry_id,
        anchor_order_id,
        inquiry_group_id,
        inquiry_version,
        inquiry_status,
        member_snapshot,
    ) in inquiry_rows:
        order_inquiries[anchor_order_id] = inquiry_status
        if inquiry_group_id is not None:
            group_inquiries[inquiry_group_id] = {
                "status": inquiry_status,
                "version": inquiry_version,
                "member_snapshot": member_snapshot or [],
            }
    gids = {o.group_id for o in orders if o.group_id is not None}
    groups = {g.id: g for g in db.query(OrderGroup).filter(OrderGroup.id.in_(gids)).all()}
    # Only ownership aggregates cross the user boundary, never foreign PO data.
    owners = defaultdict(set)
    for gid, owner in db.query(Order.group_id, Order.user_id).filter(
        Order.group_id.in_(gids)
    ).distinct().all():
        owners[gid].add(owner)
    buckets = defaultdict(list)
    identities = {}
    for order in orders:
        metadata = order.order_metadata or {}
        anomaly_data = order.anomaly_data or {}
        anomaly_count = actionable_counts.get(order.id, 0)
        if 'requires_human_review' in anomaly_data:
            requires_human_review = bool(anomaly_data['requires_human_review']) or anomaly_count > 0
        else:
            requires_human_review = anomaly_count > 0

        def value(field, current_order=order, current_metadata=metadata):
            return getattr(current_order, field, None) or current_metadata.get(field)

        identity, reason = grouping_identity(order, ports)
        identities[order.id] = identity
        gid = order.group_id if order.group_id in groups else None
        if gid is None and metadata.get(MARKER, {}).get('mode') == 'manual':
            reason = '已手动移至未分类' + (f'；{reason}' if reason else '')
        elif gid is None and not reason:
            reason = '信息完整，待自动归类'
        buckets[gid].append({
            'id': order.id, 'po_number': value('po_number'),
            'filename': order.filename, 'document_id': order.document_id,
            'product_count': order.product_count or 0,
            'ship': value('ship_name'), 'day': normalized_date(value('loading_date')),
            'port': ports[order.port_id].name if order.port_id in ports else None,
            'status': order.status, 'fulfillment_status': order.fulfillment_status or 'pending',
            'requires_human_review': requires_human_review,
            'anomaly_count': anomaly_count,
            'inquiry_status': (
                group_inquiries[gid]["status"] if gid in group_inquiries
                else order_inquiries.get(order.id)
            ),
            'reason': reason if gid is None else None,
        })
    result = []
    for gid, members in buckets.items():
        if gid is None:
            continue
        group = groups[gid]
        valid = [identities[o['id']] for o in members]
        same = all(valid) and len({identity_key(i) for i in valid}) == 1
        identity = valid[0] if same else None
        member_ships = {
            item['ship'] for item in valid if item and item.get('ship')
        }
        if len(member_ships) == 1:
            ship_label = next(iter(member_ships))
        elif len(member_ships) > 1:
            ship_label = '多船名'
        else:
            ship_label = group.ship_name or '船名未填写'
        can_manage = admin or (group.user_id == user_id and owners[gid] == {user_id})
        latest_inquiry = group_inquiries.get(gid)
        current_member_ids = {item["id"] for item in members}
        snapshot_member_ids = {
            item.get("order_id")
            for item in (latest_inquiry or {}).get("member_snapshot", [])
            if item.get("order_id") is not None
        }
        result.append({
            'id': gid, 'name': group.name,
            'ship': ship_label,
            'day': identity['day'] if identity else group.loading_date,
            'date_basis': identity['date_basis'] if identity else ('loading_date' if group.loading_date else None),
            'port_id': identity['port_id'] if identity else None,
            'port': identity['port_label'] if identity else '多港口 / 待确认',
            'manual': any((o.order_metadata or {}).get(MARKER, {}).get('mode') != 'auto' for o in orders if o.group_id == gid),
            'can_manage': can_manage,
            'can_generate_inquiry': can_manage and len(owners[gid]) == 1,
            'inquiry_version': (latest_inquiry or {}).get('version'),
            'inquiry_members_changed': bool(
                latest_inquiry and current_member_ids != snapshot_member_ids
            ),
            'inquiry_member_diff': {
                'added_order_ids': sorted(current_member_ids - snapshot_member_ids),
                'removed_order_ids': sorted(snapshot_member_ids - current_member_ids),
            } if latest_inquiry and current_member_ids != snapshot_member_ids else None,
            'orders': members,
        })
    return {'arrangements': result, 'unclassified': buckets[None], 'total_orders': len(orders)}


def get_arrangement_workspace(db, *, user_id, group_id):
    """Return the structured business workspace for one arrangement.

    Visibility is inherited from :func:`list_arrangements`: non-admin users
    only receive their own PO rows even when an administrator has formed a
    mixed-owner display group.  Supplier aggregates are rebuilt from those
    visible rows (or the visible subset of the latest immutable snapshot), so
    this endpoint cannot leak foreign PO numbers or counts.
    """
    listing = list_arrangements(db, user_id=user_id)
    arrangement = next(
        (item for item in listing['arrangements'] if item['id'] == group_id),
        None,
    )
    if arrangement is None:
        raise NotFound('供船订单不存在')

    visible_order_ids = {item['id'] for item in arrangement['orders']}
    orders = (
        db.query(Order)
        .options(load_only(
            Order.id,
            Order.po_number,
            Order.filename,
            Order.product_count,
            Order.products,
            Order.match_results,
            Order.match_statistics,
            Order.anomaly_data,
            Order.status,
            Order.created_at,
            Order.updated_at,
            Order.processed_at,
        ))
        .filter(Order.id.in_(visible_order_ids))
        .order_by(Order.created_at.asc(), Order.id.asc())
        .all()
    )
    orders_by_id = {order.id: order for order in orders}

    latest = (
        db.query(Inquiry)
        .filter(Inquiry.group_id == group_id)
        .order_by(Inquiry.version.desc(), Inquiry.id.desc())
        .first()
    )
    latest_suppliers = {}
    if latest is not None:
        latest_suppliers = {
            row.supplier_id: row
            for row in db.query(InquirySupplier)
            .filter(InquirySupplier.inquiry_id == latest.id)
            .all()
        }
    available_templates = db.query(SupplierTemplate).all()

    # A completed inquiry snapshot is the strongest audit source.  A queued
    # version may still have an empty snapshot, so fall back to the current PO
    # match results while preserving each row's PO provenance.
    source_rows = list(latest.match_snapshot or []) if latest is not None else []
    if not source_rows:
        source_rows = []
        for order in orders:
            for index, row in enumerate(order.match_results or [], start=1):
                if not isinstance(row, dict):
                    continue
                source_rows.append({
                    **row,
                    'source_order_id': order.id,
                    'source_po_number': order.po_number,
                    'source_line': row.get('source_line') or index,
                })

    supplier_buckets = defaultdict(lambda: {
        'product_count': 0,
        'order_ids': set(),
        'po_numbers': set(),
    })
    unassigned = []
    for row in source_rows:
        if not isinstance(row, dict):
            continue
        source_order_id = row.get('source_order_id')
        if source_order_id not in visible_order_ids:
            continue
        matched_product = row.get('matched_product') or {}
        supplier_id = matched_product.get('supplier_id') if isinstance(matched_product, dict) else None
        if (
            row.get('match_status') != 'matched'
            or supplier_id is None
            or row.get('inquiry_eligibility') == 'excluded'
            or row.get('inquiry_exclusion_code')
        ):
            unassigned.append(row)
            continue
        bucket = supplier_buckets[int(supplier_id)]
        bucket['product_count'] += 1
        bucket['order_ids'].add(source_order_id)
        po_number = row.get('source_po_number') or orders_by_id[source_order_id].po_number
        if po_number:
            bucket['po_numbers'].add(str(po_number))

    supplier_names = {
        supplier.id: supplier.name
        for supplier in db.query(Supplier).filter(
            Supplier.id.in_(supplier_buckets.keys())
        ).all()
    } if supplier_buckets else {}
    suppliers = []
    for supplier_id, bucket in sorted(
        supplier_buckets.items(),
        key=lambda item: (supplier_names.get(item[0]) or '', item[0]),
    ):
        state = latest_suppliers.get(supplier_id)
        configured_template, configured_method, _ = resolve_supplier_template(
            supplier_id, available_templates
        )
        suppliers.append({
            'supplier_id': supplier_id,
            'supplier_name': (
                (state.supplier_name if state is not None else None)
                or supplier_names.get(supplier_id)
                or f'供应商 #{supplier_id}'
            ),
            'product_count': bucket['product_count'],
            'source_order_count': len(bucket['order_ids']),
            'source_po_numbers': sorted(bucket['po_numbers']),
            'status': state.status if state is not None else 'ready',
            'error_message': state.error_message if state is not None else None,
            'template_id': (
                state.template_id if state is not None and state.template_id is not None else (
                    configured_template.id if configured_template is not None else None
                )
            ),
            'template_name': (
                state.template_name if state is not None and state.template_name else (
                    configured_template.template_name
                    if configured_template is not None else None
                )
            ),
            'template_method': (
                state.template_selection_method
                if state is not None and state.template_id is not None
                else configured_method
            ),
        })

    po_rows = []
    summaries_by_id = {
        item['id']: item for item in arrangement['orders']
    }
    total_products = 0
    matched_products = 0
    total_anomalies = 0
    timestamps: list[datetime] = []
    for order in orders:
        summary = summaries_by_id[order.id]
        stats = order.match_statistics or {}
        product_count = order.product_count or len(order.products or [])
        matched_count = int(stats.get('matched') or 0)
        unmatched_count = int(stats.get('not_matched') or max(product_count - matched_count, 0))
        total_products += product_count
        matched_products += matched_count
        total_anomalies += int(summary.get('anomaly_count') or 0)
        for value in (order.created_at, order.updated_at, order.processed_at):
            if value is not None:
                timestamps.append(value)
        po_rows.append({
            **summary,
            'created_at': order.created_at,
            'updated_at': order.updated_at,
            'processed_at': order.processed_at,
            'matched_count': matched_count,
            'unmatched_count': unmatched_count,
        })

    if latest is not None:
        for value in (latest.created_at, latest.updated_at, latest.completed_at):
            if value is not None:
                timestamps.append(value)

    activity = []
    for order in orders:
        if order.created_at is not None:
            activity.append({
                'occurred_at': order.created_at,
                'kind': 'po_received',
                'message': f'{order.po_number or order.filename} 已加入供船订单',
                'order_id': order.id,
            })
        if order.processed_at is not None:
            activity.append({
                'occurred_at': order.processed_at,
                'kind': 'po_processed',
                'message': f'{order.po_number or order.filename} 自动处理完成',
                'order_id': order.id,
            })
    if latest is not None:
        occurred_at = latest.completed_at or latest.updated_at or latest.created_at
        if occurred_at is not None:
            activity.append({
                'occurred_at': occurred_at,
                'kind': 'inquiry',
                'message': f'询价版本 {latest.version}：{latest.status}',
                'order_id': None,
            })
    activity.sort(key=lambda item: item['occurred_at'], reverse=True)

    return {
        'arrangement': {**arrangement, 'orders': po_rows},
        'summary': {
            'product_count': total_products,
            'matched_count': matched_products,
            'unmatched_count': max(total_products - matched_products, len(unassigned)),
            'supplier_count': len(suppliers),
            'anomaly_count': total_anomalies,
            'updated_at': max(timestamps) if timestamps else None,
        },
        'latest_inquiry': ({
            'id': latest.id,
            'version': latest.version,
            'status': latest.status,
            'started_at': latest.started_at,
            'completed_at': latest.completed_at,
            'unassigned_count': latest.unassigned_count,
            'error_message': latest.error_message,
            'inputs_changed': bool(
                (latest.completed_at or latest.created_at)
                and any(
                    order.updated_at
                    and order.updated_at > (latest.completed_at or latest.created_at)
                    for order in orders
                )
            ),
        } if latest is not None else None),
        'suppliers': suppliers,
        'unassigned_items': [
            {
                'source_order_id': item.get('source_order_id'),
                'source_po_number': item.get('source_po_number'),
                'source_line': item.get('source_line') or item.get('source_line_number'),
                'product_name': item.get('product_name') or item.get('original_name'),
                'reason': item.get('match_reason') or '未找到匹配商品或供应商',
            }
            for item in unassigned
        ],
        'activity': activity[:20],
    }


def update_arrangement(
    db, *, user_id: int, group_id: int, body: SupplyArrangementUpdate
):
    """Update the shared ship/date/port fields for every member PO atomically."""
    lock_grouping(db)
    group = require_manage(db, group_id, user_id)
    port = db.get(Port, body.port_id)
    if port is None:
        raise BadRequest('目标港口不存在')
    if port.country_id is None:
        raise BadRequest('目标港口没有国家信息')
    orders = db.query(Order).filter(Order.group_id == group_id).all()
    if not orders:
        raise BadRequest('供船订单中没有 PO')

    loading_date = body.loading_date.isoformat()
    group.ship_name = body.ship_name.strip()
    group.loading_date = loading_date
    group.updated_at = datetime.utcnow()
    for order in orders:
        order.ship_name = group.ship_name
        order.loading_date = loading_date
        order.port_id = port.id
        order.country_id = port.country_id
        # The selected master-data port is the sole target port. Keep the
        # promoted column and metadata projection aligned for old readers.
        order.destination_port = port.name
        metadata = dict(order.order_metadata or {})
        metadata.update({
            'ship_name': group.ship_name,
            'loading_date': loading_date,
            'destination_port': port.name,
        })
        order.order_metadata = metadata
        flag_modified(order, 'order_metadata')
        mark_manual(order)
    db.commit()

    # Port/date changes alter the candidate product pool. Rebuild matching now
    # so the page never displays results from the previous destination.
    from domains.orders.groups.matching import match_arrangement

    try:
        match_arrangement(db, group_id)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return get_arrangement_workspace(db, user_id=user_id, group_id=group_id)


def classify_order(db, *, user_id, order_id):
    """Explicitly relinquish a manual choice and retry the existing rules."""
    lock_grouping(db)
    query = db.query(Order).options(load_only(Order.id, Order.user_id, Order.group_id, Order.order_metadata)).filter(Order.id == order_id)
    if not _admin(db, user_id):
        query = query.filter(Order.user_id == user_id)
    order = query.first()
    if order is None:
        raise NotFound('订单不存在')
    # Detach this PO first: other members may belong to a manually managed group.
    order.group_id = None
    order.order_metadata = {**(order.order_metadata or {}), MARKER: {'mode': 'auto'}}
    db.flush()
    result = regroup(db, apply=True, focus_order_id=order.id)
    reason = next((item['reason'] for item in result['skipped'] if item['order_id'] == order.id), None)
    return {'group_id': order.group_id, 'reason': reason}
