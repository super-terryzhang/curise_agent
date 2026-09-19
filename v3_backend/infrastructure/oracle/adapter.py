"""Bounded external integrations: read-only Oracle/catalog, validated recognition cache."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from .vendor.extraction import gemini_structure, read_pdf, validate_structure

HOST = "ibqkjb.fa.ocs.oraclecloud.com"
RESOURCE = "/fscmRestApi/resources/11.13.18.05/purchaseOrders"
FIELDS = "POHeaderId,OrderNumber,Status,StatusCode,CreationDate,LastUpdateDate,Revision"


class IntegrationError(RuntimeError):
    def __init__(self, code, retryable=False, *, field=None):
        super().__init__(code)
        self.code, self.retryable, self.field = code, retryable, field


def canonical_date(value, *, field=None):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(UTC).isoformat()
    except (TypeError, ValueError, AttributeError):
        raise IntegrationError("ORACLE_INVALID_DATE", field=field) from None


def _encoded(value):
    # Exact serialization of historical content-addressed recognition envelopes.
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def identity(record, host=HOST):
    if not isinstance(record, dict):
        raise IntegrationError("ORACLE_INVALID_IDENTITY", field="record")
    if not record.get("POHeaderId"):
        raise IntegrationError("ORACLE_INVALID_IDENTITY", field="POHeaderId")
    if not isinstance(record.get("OrderNumber"), str) or not record["OrderNumber"].strip():
        raise IntegrationError("ORACLE_INVALID_IDENTITY", field="OrderNumber")
    if type(record.get("Revision")) is not int or record["Revision"] < 0:
        raise IntegrationError("ORACLE_INVALID_IDENTITY", field="Revision")
    if not isinstance(record.get("StatusCode"), str):
        raise IntegrationError("ORACLE_INVALID_IDENTITY", field="StatusCode")
    source = [
        host,
        str(record["POHeaderId"]),
        record["OrderNumber"],
        canonical_date(record.get("CreationDate"), field="CreationDate"),
    ]
    version = [record.get("Revision"), canonical_date(record.get("LastUpdateDate"), field="LastUpdateDate"), record.get("StatusCode")]
    return {"source_key": _sha(_encoded(source)), "version_key": _sha(_encoded(version))}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OracleClient:
    """config: username/password plus optional host. Host must match the verified tenant."""

    def __init__(self, config):
        self.host = config.get("host", HOST)
        if self.host != HOST:
            raise IntegrationError("UNVERIFIED_ORACLE_HOST")
        username, password = config.get("username"), config.get("password")
        if not username or not password:
            raise IntegrationError("ORACLE_CREDENTIALS_MISSING")
        self.auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, url, body=None):
        parts = urllib.parse.urlsplit(url)
        try:
            safe = (
                parts.scheme == "https"
                and parts.hostname == self.host
                and parts.port in (None, 443)
                and not parts.username
                and not parts.password
                and not parts.fragment
            )
        except ValueError:
            safe = False
        path_allowed = (
            parts.path == RESOURCE
            if body is None
            else bool(
                re.fullmatch(
                    r"/fscmRestApi/resources/(?:[0-9.]+|latest)/purchaseOrders/[^/]+/action/viewPDF",
                    parts.path,
                )
            )
            and not parts.query
        )
        if not safe or not path_allowed:
            raise IntegrationError("ORACLE_OPERATION_NOT_ALLOWED")
        headers = {"Authorization": self.auth, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/vnd.oracle.adf.action+json"
        request = urllib.request.Request(
            url, data=body, headers=headers, method="GET" if body is None else "POST"
        )
        try:
            with self.opener.open(request, timeout=90) as response:
                if response.status != 200:
                    raise IntegrationError("ORACLE_HTTP_" + str(response.status))
                raw = response.read(60 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            raise IntegrationError(
                f"ORACLE_HTTP_{error.code}", error.code in (408, 429) or error.code >= 500
            ) from None
        except (urllib.error.URLError, OSError):
            raise IntegrationError("ORACLE_NETWORK_ERROR", True) from None
        if len(raw) > 60 * 1024 * 1024:
            raise IntegrationError("ORACLE_RESPONSE_TOO_LARGE")
        try:
            data = json.loads(raw)
        except ValueError:
            raise IntegrationError("ORACLE_INVALID_JSON") from None
        if not isinstance(data, dict):
            raise IntegrationError("ORACLE_INVALID_RESPONSE")
        return data

    def list_orders(self, *, record_issues=None):
        """List valid records; an explicit collector isolates malformed individual rows."""
        result, seen, offset = [], set(), 0
        # No status predicate: a successful scan includes all supplier-visible statuses.
        for _ in range(10000):
            query = urllib.parse.urlencode(
                {
                    "limit": 500,
                    "offset": offset,
                    "orderBy": "POHeaderId:asc",
                    "fields": FIELDS,
                    "links": "self,canonical",
                }
            )
            page = self.request(f"https://{self.host}{RESOURCE}?{query}")
            items, more = page.get("items"), page.get("hasMore")
            if not isinstance(items, list) or type(more) is not bool or page.get("offset") != offset:
                raise IntegrationError("ORACLE_PAGINATION_INVALID")
            for index, record in enumerate(items):
                try:
                    keys = identity(record, self.host)
                except IntegrationError as error:
                    if record_issues is None:
                        raise
                    number = record.get("OrderNumber") if isinstance(record, dict) else None
                    status = record.get("StatusCode") if isinstance(record, dict) else None
                    record_issues.append({
                        "po_number": number if isinstance(number, str) and number.strip() and len(number) <= 128 else f"Oracle row {offset + index + 1}",
                        "oracle_status": status if isinstance(status, str) else None,
                        "code": error.code,
                        "field": error.field,
                    })
                    continue
                if keys["source_key"] in seen:
                    raise IntegrationError("ORACLE_PAGINATION_DUPLICATE")
                seen.add(keys["source_key"])
                result.append(record)
            if not more:
                return result
            size = page.get("limit")
            if not items or type(size) is not int or size <= 0:
                raise IntegrationError("ORACLE_PAGINATION_STALLED")
            offset += size
        raise IntegrationError("ORACLE_PAGINATION_LIMIT")

    def download(self, record):
        identity(record, self.host)
        links = record.get("links", [])
        href = next(
            (
                item.get("href")
                for rel in ("canonical", "self")
                for item in links
                if item.get("rel") == rel and item.get("href")
            ),
            None,
        )
        if not isinstance(href, str):
            raise IntegrationError("ORACLE_RESOURCE_LINK_MISSING")
        parts = urllib.parse.urlsplit(urllib.parse.urljoin(f"https://{self.host}{RESOURCE}/", href))
        url = urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path.rstrip("/") + "/action/viewPDF", "", "")
        )
        result = self.request(url, b"{}").get("result")
        if not isinstance(result, str):
            raise IntegrationError("ORACLE_PDF_MISSING")
        if "," in result:
            pieces = result.split(",", 2)
            if len(pieces) != 3 or pieces[1].strip().lower() != "application/pdf":
                raise IntegrationError("ORACLE_PDF_ENVELOPE_INVALID")
            result = pieces[2]
        try:
            pdf = base64.b64decode("".join(result.split()), validate=True)
        except ValueError:
            raise IntegrationError("ORACLE_PDF_BASE64_INVALID") from None
        if not pdf.startswith(b"%PDF-") or b"%%EOF" not in pdf[-4096:]:
            raise IntegrationError("ORACLE_PDF_INCOMPLETE")
        return pdf


def recognition_key(pdf_sha, po, model="gemini-3.5-flash"):
    return _sha(
        _encoded(
            [
                "oracle-po-extraction-v2",
                {"pdf_sha256": pdf_sha, "po": po, "model": model, "text_extractor": "pypdfium2-4.30.0"},
            ]
        )
    )


def normalize_identity(raw, text, record):
    """Normalize a displayed change-order suffix only when independent source evidence agrees."""
    if not isinstance(raw, dict):
        raise IntegrationError("RECOGNITION_OBJECT_INVALID")
    original = raw.get("po_number")
    base, revision = record.get("OrderNumber"), record.get("Revision")
    result = deepcopy(raw)
    provenance = {
        "raw_po_number": original,
        "canonical_po_number": original,
        "normalized": False,
        "identity_verified": original == base,
        "rule_version": "oracle-change-order-identity-v1",
        "evidence": [],
    }
    if original == base:
        return result, provenance
    if not isinstance(base, str) or not base or type(revision) is not int or revision < 1:
        return result, provenance
    expected = f"{base}-{revision}"
    if original != expected or not text.get("pages"):
        return result, provenance
    printed = []
    for page in text["pages"]:
        printed.extend(re.findall(r"Purchase Order Number:[ \t]*\r?\n?[ \t]*([^\s]+)", page["text"]))
    if not printed or set(printed) != {expected}:
        return result, provenance
    first = " ".join(text["pages"][0]["text"].split())
    change = re.search(
        r"CERTAIN DETAILS OF PURCHASE ORDER\s+"
        + re.escape(base)
        + r"\s+HAVE BEEN MODIFIED.{0,180}?DO NOT DUPLICATE THIS ORDER\.",
        first,
    )
    confirmation = re.search(r"COPY OF .{0,100}?PURCHASE ORDER\s+" + re.escape(base) + r"\.", first)
    if not change or not confirmation or "CHANGE ORDER" not in first:
        return result, provenance
    result["po_number"] = base
    provenance.update(
        {
            "canonical_po_number": base,
            "normalized": True,
            "identity_verified": True,
            "oracle_revision": revision,
            "evidence": [
                {"page": 1, "text": change[0]},
                {"page": 1, "text": confirmation[0]},
                {"field": "printed_order_number", "value": expected, "occurrences": len(printed)},
            ],
        }
    )
    return result, provenance


def structure(pdf, record, cache_dir, *, api_key=None, model="gemini-3.5-flash", allow_model=False):
    text = read_pdf(pdf)
    pdf_sha = _sha(pdf)
    po = record["OrderNumber"]
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (recognition_key(pdf_sha, po, model) + ".json")
    cache_hit = path.exists()
    if cache_hit:
        try:
            envelope = json.loads(path.read_text())
        except (ValueError, OSError):
            raise IntegrationError("RECOGNITION_CACHE_INVALID") from None
        if envelope.get("sha256") != _sha(_encoded(envelope.get("value"))):
            raise IntegrationError("RECOGNITION_CACHE_CORRUPT")
        raw = envelope["value"]
    else:
        if not allow_model:
            raise IntegrationError("RECOGNITION_CACHE_MISSING_MODEL_DISABLED")
        raw = gemini_structure(pdf, text, key=api_key, model=model)
        payload = _encoded({"sha256": _sha(_encoded(raw)), "value": raw})
        fd, temporary = tempfile.mkstemp(prefix=".recognition-", dir=cache)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    normalized, identity_evidence = normalize_identity(raw, text, record)
    try:
        validated = validate_structure(normalized, text, po, "gemini")
        quality = {
            "complete": validated["complete"],
            "issues": validated["quality_issues"],
            "source_row_check": validated.get("source_row_check"),
            "routing_evidence": validated.get("routing_evidence"),
        }
    except ValueError as error:
        validated = normalized
        quality = {"complete": False, "issues": ["STRUCTURE_VALIDATION_FAILED"], "reason": str(error)}
    from .vendor.source_checks import routing_fields

    source_routing = routing_fields(text)
    metadata_value = validated.get("metadata") or {}
    routing_agrees = all(
        metadata_value.get(name) == value
        for name, value in source_routing["metadata"].items()
        if name != "destination_name" and value
    )
    quality["identity_verified"] = identity_evidence["identity_verified"]
    quality["routing_verified"] = bool(
        identity_evidence["identity_verified"]
        and source_routing["complete"]
        and routing_agrees
        and text["text_pages"] == text["page_count"]
    )
    quality["routing_evidence"] = source_routing
    quality["identity_evidence"] = identity_evidence
    if not isinstance(validated, dict):
        raise IntegrationError("RECOGNITION_OBJECT_INVALID")

    def nullable(value):
        return value if value is not None and str(value).strip() else None

    metadata = validated.get("metadata") or {}
    document = {
        "schema_version": "1",
        "po_number": nullable(validated.get("po_number")),
        "metadata": {
            key: nullable(metadata.get(key))
            for key in [
                "ship_name",
                "loading_date",
                "delivery_date",
                "destination_name",
                "location_code",
                "cruise_reference",
            ]
        },
        "lines": [],
    }
    for index, row in enumerate(validated.get("lines") or []):
        document["lines"].append(
            {
                "line_id": f"line-{index + 1:05d}",
                **{
                    key: nullable(row.get(key))
                    for key in [
                        "source_line",
                        "page",
                        "product_code",
                        "description",
                        "quantity",
                        "unit",
                        "evidence",
                    ]
                },
            }
        )
    return {
        "document": document,
        "quality": quality,
        "cache_hit": cache_hit,
        "pdf_sha256": pdf_sha,
        "model": model,
        "recognition_version": "oracle-po-extraction-v2",
        "validation_version": "scheduler-source-validation-v2",
        "provenance": {
            "raw_model_result": deepcopy(raw),
            "raw_model_sha256": _sha(_encoded(raw)),
            "identity": identity_evidence,
        },
    }
