"""Single Oracle PO -> Document -> Order -> supplier inquiries.

Explicit synchronous steps, no fire-and-forget jobs. Batch scheduling and
revision adoption are deliberately separate from this first delivery.
"""

import hashlib
import re
from datetime import datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from domains.document.models import Document
from domains.document.service import store_source_document
from domains.identity.models import User
from domains.inquiry.automation import prepare_inquiry
from domains.inquiry.models import InquirySupplier
from domains.inquiry.orchestrator import run_inquiry_for_group
from domains.orders import anomaly as anomaly_engine
from domains.orders.automation import mark_pipeline_stage, new_pipeline_trace
from domains.orders.matching.automation import match_for_import
from domains.orders.models import Order
from domains.orders.oracle_models import OraclePOImport
from domains.orders.projection import project_purchase_order
from infrastructure.db import session as sessions
from infrastructure.oracle.adapter import IntegrationError, identity, structure
from infrastructure.oracle.vendor.extraction import read_pdf
from infrastructure.storage import get_storage


def result_of(source):
    return {
        k: getattr(source, k)
        for k in (
            "source_key",
            "version_key",
            "po_number",
            "status",
            "stage",
            "document_id",
            "order_id",
            "inquiry_id",
            "issues",
            "error_code",
        )
    }


def _stop(db, source, issues, *, failed=False):
    source.status = "failed" if failed else "needs_review"
    source.issues = issues
    message = "Oracle 自动处理：" + "; ".join(i["code"] for i in issues)
    if source.document_id:
        doc = db.get(Document, source.document_id)
        if doc:
            doc.processing_error = message
            if failed and not source.order_id:
                doc.status = "error"
    if source.order_id:
        order = db.get(Order, source.order_id)
        if order:
            order.processing_error = message
            order.status = "error" if failed else "extracted"
    db.commit()
    return result_of(source)


def _historical_candidates(db, record):
    keys = {record["OrderNumber"]}
    if type(record.get("Revision")) is int and record["Revision"] > 0:
        # Candidate detection only; never strip a suffix or automatically adopt.
        keys.add(f"{record['OrderNumber']}-{record['Revision']}")
    orders = [
        o.id
        for o in db.query(Order).all()
        if keys.intersection({o.po_number, (o.order_metadata or {}).get("po_number")})
    ]
    documents = [
        d.id
        for d in db.query(Document).filter(Document.doc_type == "purchase_order").all()
        if keys.intersection(
            {
                (d.extracted_data or {}).get("po_number"),
                ((d.extracted_data or {}).get("metadata") or {}).get("po_number"),
            }
        )
    ]
    return orders, documents


def _apply_analysis(doc, record, analyzed, pdf):
    parsed = analyzed["document"]
    metadata = dict(parsed.get("metadata") or {})
    metadata.update(
        po_number=record["OrderNumber"], destination_port=metadata.pop("destination_name", None)
    )
    products = [
        {**line, "product_name": line.get("description"), "unit_price": None}
        for line in parsed.get("lines", [])
    ]
    text = read_pdf(pdf)
    doc.content_markdown = "\n\n".join(p["text"] for p in text["pages"])
    # Reuse independently reconciled source amount rows, preserving customer prices.
    checked = analyzed.get("quality", {}).get("source_row_check") or {}
    source_rows = {(str(r["page"]), str(r["source_line"])): r for r in checked.get("rows", [])}
    if checked.get("verified"):
        for product in products:
            row = source_rows.get((str(product.get("page")), str(product.get("source_line"))))
            if row and len(row.get("evidence", [])) == 4:
                _, _, price, total = row["evidence"]
                product["unit_price"] = float(Decimal(price.replace(",", "")))
                product["total_price"] = float(Decimal(total.replace(",", "")))
    dates = set(
        re.findall(r"Date Of Order:\s*([0-9]{2}-[A-Za-z]{3}-[0-9]{4})", doc.content_markdown)
    )
    if len(dates) == 1:
        metadata["order_date"] = datetime.strptime(dates.pop(), "%d-%b-%Y").date().isoformat()
    currencies = set(re.findall(r"\bTotal:\s*([A-Z]{3})\s+[\d,]+\.\d+", doc.content_markdown))
    if len(currencies) == 1:
        metadata["currency"] = currencies.pop()
    doc.extracted_data = {"metadata": metadata, "products": products, "oracle_analysis": analyzed}
    doc.extraction_method = "oracle_po_gemini"
    doc.status = "extracted"
    doc.extracted_at = datetime.utcnow()
    doc.processing_error = None
    doc.tags = list(
        dict.fromkeys(
            [
                *(doc.tags or []),
                "doc_type:purchase_order",
                "source:oracle",
                "has_products:" + str(bool(products)).lower(),
            ]
        )
    )


def _source_checks(doc):
    """Verify business dates/amounts independently of model assertions."""
    text = doc.content_markdown or ""
    data = doc.extracted_data or {}
    metadata = data.get("metadata") or {}
    raw_dates = re.findall(
        r"(?:Requested Delivery Date:|Supplier delivery date to the address above:)\s*([^\r\n]+)",
        text,
    )
    dates = set()
    for raw in raw_dates:
        for fmt in ("%Y-%m-%d", "%d-%b-%Y"):
            try:
                dates.add(datetime.strptime(raw.strip(), fmt).date().isoformat())
                break
            except ValueError:
                pass
    issues = []
    if dates != {metadata.get("delivery_date")}:
        issues.append({"code": "SOURCE_DELIVERY_DATE_UNVERIFIED"})
    for row in data.get("products", []):
        if row.get("unit_price") is None or row.get("total_price") is None:
            issues.append({"code": "SOURCE_PRICE_UNVERIFIED", "line_id": row.get("line_id")})
            continue
        quantity, price, total = (
            Decimal(str(row[k])) for k in ("quantity", "unit_price", "total_price")
        )
        if (
            not all(v.is_finite() for v in (quantity, price, total))
            or price < 0
            or total < 0
            or abs(quantity * price - total) > Decimal("0.01")
        ):
            issues.append({"code": "SOURCE_AMOUNT_MISMATCH", "line_id": row.get("line_id")})
    return issues


_ISSUE_MESSAGES = {
    "SOURCE_ROWS_UNVERIFIED": "部分商品行无法与 PO 原文逐行核对",
    "SOURCE_PRICE_UNVERIFIED": "客户单价或行金额无法从 PO 原文确认",
    "SOURCE_AMOUNT_MISMATCH": "数量 × 客户单价与行金额不一致",
    "SOURCE_DELIVERY_DATE_UNVERIFIED": "交付日期无法与 PO 原文确认",
    "EXTRACTION_INCOMPLETE": "内容提取结果不完整",
    "EXACT_UNIQUE_MATCH_REQUIRED": "未找到唯一的精确商品匹配",
    "SUPPLIER_REQUIRED": "匹配商品未配置供应商",
    "QUANTITY_INVALID": "数量必须是大于 0 的数字",
    "UNIT_REQUIRED": "订购单位或供应商单位缺失",
    "UNIT_CONVERSION_EVIDENCE_REQUIRED": "订购单位不同，需要人工确认换算关系",
    "UNIT_CONVERSION_INCONSISTENT": "已提供的单位换算关系不一致",
    "TEMPLATE_BINDING_REQUIRED": "供应商询价模板无法唯一确定",
    "TEMPLATE_FIELDS_REQUIRED": "供应商模板缺少询价必填字段",
    "DELIVERY_DATE_REQUIRED": "交付日期缺失",
    "DESTINATION_REQUIRES_REVIEW": "目标港口无法唯一确定",
    "ARRANGEMENT_REQUIRED": "缺少装船日或目标港口，暂时无法归入供船安排",
}


def _oracle_findings(order, issues):
    products = {
        row.get("line_id"): {**row, "row_index": index}
        for index, row in enumerate(order.products or [], start=1)
        if isinstance(row, dict)
    }
    findings = []
    for issue in issues:
        code = issue.get("code") or "ORACLE_REVIEW_REQUIRED"
        row = products.get(issue.get("line_id"), {})
        step = 3 if code.startswith("SOURCE_") or code == "EXTRACTION_INCOMPLETE" else 5
        if code in {"TEMPLATE_BINDING_REQUIRED", "TEMPLATE_FIELDS_REQUIRED", "UNIT_REQUIRED", "UNIT_CONVERSION_EVIDENCE_REQUIRED", "UNIT_CONVERSION_INCONSISTENT"}:
            step = 7
        if code in {"DESTINATION_REQUIRES_REVIEW"}:
            step = 6
        findings.append(
            anomaly_engine.finding(
                code=code,
                step=step,
                severity="warning"
                if code in {"SOURCE_PRICE_UNVERIFIED", "SOURCE_DELIVERY_DATE_UNVERIFIED"}
                else "error",
                scope="row" if row else "order",
                row=row,
                message=_ISSUE_MESSAGES.get(code, f"Oracle 自动检查发现问题：{code}"),
                suggestion="核对 PO 原文或主数据后重新运行供船安排",
                evidence={k: v for k, v in issue.items() if k != "code"},
            )
        )
    return findings


def _save_anomaly_result(db, order, pipeline, *, inquiry=None, issues=None):
    mark_pipeline_stage(pipeline, 8, "running")
    result = anomaly_engine.run_anomaly_check(
        order,
        inquiry=inquiry,
        pipeline=pipeline,
        extra_findings=_oracle_findings(order, issues or []),
    )
    mark_pipeline_stage(
        pipeline,
        8,
        "needs_review" if result["requires_human_review"] else "completed_with_warnings" if result["warning_count"] else "completed",
        evidence={
            "total": result["total_anomalies"],
            "warning": result["warning_count"],
            "error": result["error_count"],
            "blocking": result["blocking_count"],
        },
    )
    result["pipeline"] = pipeline
    order.anomaly_data = result
    flag_modified(order, "anomaly_data")
    db.commit()
    return result


def import_po(
    record,
    *,
    client,
    user_id,
    cache_dir,
    folder_id=None,
    unit_approvals=None,
    template_overrides=None,
    allow_model=False,
    api_key=None,
    model="gemini-3.5-flash",
):
    keys = identity(record)
    with sessions.SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise ValueError("ACTIVE_IMPORT_USER_REQUIRED")
        existing = db.get(OraclePOImport, keys["source_key"])
        if existing:
            if existing.user_id != user_id:
                raise ValueError("IMPORT_OWNER_MISMATCH")
            if existing.version_key != keys["version_key"]:
                return {
                    **result_of(existing),
                    "status": "needs_review",
                    "issues": [{"code": "REVISION_REQUIRES_ADOPTION"}],
                }
            return result_of(existing)
        if record.get("StatusCode") != "OPEN":
            raise ValueError("OPEN_PO_REQUIRED")
        source = OraclePOImport(
            **keys,
            po_number=record["OrderNumber"],
            user_id=user_id,
            source_record={k: v for k, v in record.items() if k != "links"},
            status="processing",
            stage="download",
        )
        db.add(source)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.get(OraclePOImport, keys["source_key"])
            if existing is None:
                raise
            if existing.user_id != user_id or existing.version_key != keys["version_key"]:
                raise ValueError("CONCURRENT_IMPORT_CONFLICT") from None
            return result_of(existing)
        try:
            orders, documents = _historical_candidates(db, record)
            if orders or documents:
                return _stop(
                    db,
                    source,
                    [
                        {
                            "code": "EXISTING_PO_REQUIRES_LINK",
                            "order_ids": orders,
                            "document_ids": documents,
                        }
                    ],
                )
            pdf = client.download(record)
            source.pdf_sha256 = hashlib.sha256(pdf).hexdigest()
            doc = store_source_document(
                db,
                user_id=user_id,
                filename=record["OrderNumber"] + ".pdf",
                content=pdf,
                source_key=source.source_key,
                version_key=source.version_key,
                folder_id=folder_id,
            )
            source.document_id = doc.id
            source.stage = "analysis"
            db.commit()
            doc.status = "extracting"
            db.commit()
            analyzed = structure(
                pdf, record, cache_dir, allow_model=allow_model, api_key=api_key, model=model
            )
            _apply_analysis(doc, record, analyzed, pdf)
            db.commit()
            quality = analyzed["quality"]
            if not quality.get("identity_verified"):
                return _stop(db, source, [{"code": "SOURCE_IDENTITY_UNVERIFIED"}])
            source.stage = "order"
            order = project_purchase_order(doc, db, run_match_inline=False)
            if order.products and all(p.get("total_price") is not None for p in order.products):
                order.total_amount = sum(Decimal(str(p["total_price"])) for p in order.products)
            source.order_id = order.id
            source.stage = "matching"
            db.commit()
            pipeline = new_pipeline_trace()
            for step, evidence in (
                (1, {"source": "oracle", "source_key": source.source_key}),
                (2, {"document_id": doc.id, "pdf_sha256": source.pdf_sha256}),
                (3, {"extraction_method": doc.extraction_method, "product_count": len(order.products or [])}),
                (4, {"order_id": order.id}),
            ):
                mark_pipeline_stage(pipeline, step, "completed", evidence=evidence)
            # Directory/voyage completeness is not a v3 matching requirement.
            issues = [
                {"code": c} for c in quality.get("issues", []) if c != "ROUTING_SOURCE_UNVERIFIED"
            ]
            if not (quality.get("source_row_check") or {}).get("verified"):
                issues.append({"code": "SOURCE_ROWS_UNVERIFIED"})
            if (
                analyzed.get("provenance", {}).get("raw_model_result", {}).get("complete")
                is not True
            ):
                issues.append({"code": "EXTRACTION_INCOMPLETE"})
            issues.extend(_source_checks(doc))
            matching_issues = match_for_import(db, order)
            issues.extend(matching_issues)
            bindings = {}
            if not matching_issues:
                # Oracle projection happens before match_for_import selects the
                # canonical port_id. Re-run grouping now that both loading day
                # and selected port are available; otherwise a valid automatic
                # PO remains in the unclassified bucket.
                from domains.orders.groups.automation import auto_group_order

                # SessionLocal intentionally has autoflush disabled. Persist
                # match_results + port_id before regroup() uses populate_existing,
                # or that read would replace the pending values with the previous
                # database state and prepare_inquiry would see zero line coverage.
                db.flush()
                auto_group_order(db, order.id)
                db.refresh(order)
                preparation_issues, bindings = prepare_inquiry(
                    db,
                    order,
                    source,
                    unit_approvals=unit_approvals,
                    template_overrides=template_overrides,
                )
                issues.extend(preparation_issues)
            match_stats = order.match_statistics or {}
            mark_pipeline_stage(
                pipeline,
                5,
                "completed_with_anomalies" if issues or match_stats.get("not_matched") else "completed",
                evidence=match_stats,
            )
            if order.group_id is None:
                mark_pipeline_stage(
                    pipeline,
                    6,
                    "needs_review",
                    message=_ISSUE_MESSAGES["ARRANGEMENT_REQUIRED"],
                    error_code="ARRANGEMENT_REQUIRED",
                    suggestion="补充装船日和目标港口后重新自动归组",
                )
            else:
                mark_pipeline_stage(
                    pipeline, 6, "completed", evidence={"group_id": order.group_id}
                )
            # Recheck the Oracle source immediately before creating outputs.
            current = [
                r for r in client.list_orders() if identity(r)["source_key"] == source.source_key
            ]
            if len(current) != 1 or identity(current[0])["version_key"] != source.version_key:
                return _stop(db, source, [{"code": "SOURCE_CHANGED_DURING_IMPORT"}])
            source.stage = "inquiry"
            order.status = "ready"
            order.processing_error = None
            db.commit()
            if order.group_id is None:
                mark_pipeline_stage(pipeline, 7, "skipped", message="等待供船安排归组")
                _save_anomaly_result(db, order, pipeline, issues=issues)
                return _stop(
                    db,
                    source,
                    [*issues, {"code": "ARRANGEMENT_REQUIRED"}],
                )
            state = run_inquiry_for_group(
                order.group_id,
                template_overrides=bindings,
                max_workers=1,
            )
            db.expire_all()
            from domains.inquiry import repository as inquiry_repository

            inquiry = inquiry_repository.get_latest_inquiry_by_group(db, order.group_id)
            if inquiry is None:
                return _stop(
                    db,
                    source,
                    [{"code": "INQUIRY_OUTPUT_INCOMPLETE"}],
                    failed=True,
                )
            source.inquiry_id = inquiry.id
            suppliers = db.query(InquirySupplier).filter_by(inquiry_id=inquiry.id).all()
            completed_suppliers = [row for row in suppliers if row.status == "completed"]
            invalid_bindings = not set(bindings).issubset({r.supplier_id for r in suppliers})
            if invalid_bindings:
                issues.append({"code": "INQUIRY_OUTPUT_INCOMPLETE"})
            for supplier in completed_suppliers:
                if not supplier.excel_file_url:
                    return _stop(
                        db,
                        source,
                        [{"code": "INQUIRY_OUTPUT_INCOMPLETE"}],
                        failed=True,
                    )
                content = get_storage().download(supplier.excel_file_url)
                if not content.startswith(b"PK"):
                    return _stop(db, source, [{"code": "INQUIRY_FILE_INVALID"}], failed=True)
            mark_pipeline_stage(
                pipeline,
                7,
                "completed" if state.status == "completed" else "completed_with_anomalies",
                message=None if state.status == "completed" else "部分商品或供应商需要人工处理",
                evidence={
                    "inquiry_id": state.id,
                    "version": state.version,
                    "status": state.status,
                    "supplier_count": state.supplier_count,
                    "unassigned_count": state.unassigned_count,
                },
            )
            anomaly_result = _save_anomaly_result(
                db, order, pipeline, inquiry=state, issues=issues
            )
            source.status = (
                "needs_review" if anomaly_result["requires_human_review"] else "completed"
            )
            source.stage = "anomaly" if source.status == "needs_review" else "completed"
            source.issues = anomaly_result["findings"]
            db.commit()
            return result_of(source)
        except Exception as error:
            db.rollback()
            source = db.get(OraclePOImport, keys["source_key"])
            code = (
                error.code
                if isinstance(error, IntegrationError)
                else "IMPORT_" + type(error).__name__.upper()
            )
            source.error_code = code
            return _stop(db, source, [{"code": code}], failed=True)
