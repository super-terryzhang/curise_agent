"""Narrow Oracle text-template checks with source evidence; unknown layouts remain reviewable."""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation


def routing_fields(text):
    full = "\n".join(p["text"] for p in text["pages"])

    def one(pattern):
        values = {" ".join(x.split()) for x in re.findall(pattern, full, re.MULTILINE | re.IGNORECASE)}
        return next(iter(values)) if len(values) == 1 else None

    ship = one(r"^[ \t]*Vessel[ \t]*:[ \t]*([^\r\n]+)")
    location = one(r"^[ \t]*PORT CODE[ \t]*:[ \t]*([^\r\n]+)")
    destination = one(r"^[ \t]*FINAL DESTINATION[ \t]*:[ \t]*([^\r\n]+)")
    loading = one(r"^[ \t]*LOADING DATE[ \t]*:[ \t]*([^\r\n]+)")
    day = None
    if loading:
        for fmt in ("%b %d %Y", "%B %d %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%Y-%m-%d"):
            try:
                day = datetime.strptime(loading, fmt).date().isoformat()  # noqa: DTZ007 - business date, not instant
                break
            except ValueError:
                pass
    reference = one(r"^[ \t]*" + re.escape(ship) + r"[ \t]*:[ \t]*([A-Z0-9]+)[ \t]*\r?$") if ship else None
    metadata = {
        "ship_name": ship,
        "loading_date": day,
        "location_code": location,
        "cruise_reference": reference,
        "destination_name": destination,
    }
    return {
        "metadata": metadata,
        "complete": all([ship, location, day, reference, destination]),
        "method": "explicit Oracle labels v2; colon spacing and abbreviated/full month dates; unique across pages",
    }


def verify_rows(value, text):
    """Reconcile each printed item number, ordinal, quantity and raw unit, across all pages."""
    expected = []
    pages = text["pages"]
    if text.get("text_pages") != text["page_count"]:
        return {"verified": False, "reason": "text_layer_incomplete"}
    for page in pages:
        content = page["text"]
        headers = list(
            re.finditer(
                r"^\s*(\d+)\s+([A-Za-z0-9][A-Za-z0-9._/-]*)\s*\r?\n\s*Item Description:",
                content,
                re.MULTILINE,
            )
        )
        if re.search(r"L No\s+Item Number\s+Quantity", content) and not headers:
            return {"verified": False, "reason": "unrecognized_item_table"}
        # An item description without the recognized row header means layout coverage is unknown.
        if len(headers) != len(re.findall(r"Item Description:", content)):
            return {"verified": False, "reason": "unsupported_item_header"}
        for i, h in enumerate(headers):
            block = content[h.start() : headers[i + 1].start() if i + 1 < len(headers) else len(content)]
            amounts = re.findall(
                r"^\s*([\d,]+\.\d+)\s+(\S+)\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)\s*$", block, re.MULTILINE
            )
            if len(amounts) != 1:
                return {"verified": False, "reason": "ambiguous_item_amounts"}
            qty, unit, _price, _total = amounts[0]
            expected.append(
                {
                    "page": page["page"],
                    "source_line": h[1],
                    "product_code": h[2],
                    "quantity": qty.replace(",", ""),
                    "unit": unit,
                    "evidence": amounts[0],
                }
            )
    if not expected or len(expected) != len(value["lines"]):
        return {"verified": False, "reason": "row_count_mismatch", "source_count": len(expected)}
    for actual, wanted in zip(value["lines"], expected, strict=True):
        try:
            same = all(
                str(actual.get(k)) == str(wanted[k]) for k in ("page", "source_line", "product_code", "unit")
            )
            same = same and Decimal(str(actual.get("quantity"))) == Decimal(wanted["quantity"])
        except (InvalidOperation, ValueError):
            same = False
        if not same:
            return {"verified": False, "reason": "row_fields_mismatch", "source_count": len(expected)}
    ordinals = [int(r["source_line"]) for r in expected]
    if ordinals != list(range(1, len(expected) + 1)):
        return {"verified": False, "reason": "non_contiguous_source_lines"}
    return {
        "verified": True,
        "method": "Oracle labelled rows reconciled with quantity/unit on every text page",
        "source_count": len(expected),
        "rows": expected,
    }
