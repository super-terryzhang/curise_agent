"""Document inspection tools — what the user uploaded, sit in storage,
or were processed via the extraction pipeline.

Why these four shapes (vs "give me the whole PDF"): industry consensus
in 2025 (OpenAI Assistants `file_search`, Anthropic Files API, MCP
filesystem servers) is *search-first + paginated read*. Dumping a
50-page PDF into the LLM's context burns tokens and triggers the
mid-context-loss failure mode. Search → drill-in is faster and
cheaper.

Read paths supported:
- `list_documents`         — metadata only
- `get_document`           — single document with summary
- `search_documents`       — full-text across the user's documents
- `read_document_section`  — paginated read (PDF page or Excel sheet/range)

Skipped on purpose: `download_document` (file streaming through chat
makes no sense — frontend has a download button) and
`summarize_document` (research shows agents over-rely on summaries
and miss details — let them search + read instead).
"""

from __future__ import annotations

import json
from io import BytesIO
from typing import Any

from general_agent import ToolContext, tool
from sqlalchemy import String, cast, or_, select

from agent.runtime.deps import get_deps
from domains.document import service as doc_service
from domains.document.models import Document
from domains.document.service import DocumentError
from infrastructure.storage import get_storage

_MAX_EXTRACTED_DATA_BYTES = 5_000
_DEFAULT_SEARCH_TOP_K = 5
_MAX_SNIPPET_CHARS = 200
_MAX_LIST_LIMIT = 50
# Truncate per-row summary in list responses so 50 rows don't blow the
# context window. Single-doc views via `get_document` return the full text.
_LIST_SUMMARY_PREVIEW_CHARS = 240


@tool(toolset="business", emoji="📄")
def list_documents(
    doc_type: str = "",
    status: str = "",
    filename_like: str = "",
    tag: str = "",
    limit: int = 20,
    *,
    ctx: ToolContext,
) -> str:
    """List the user's uploaded documents — primary triage tool.

    Each row carries `tags`, `user_tags`, and a truncated `summary` so
    you can answer most "do we have a doc about X?" questions without
    ever calling `read_document_section`. For the full text drill-in,
    follow up with `get_document` or `read_document_section`.

    ALSO use this for "how many documents" / "我有几个文档" — the response
    includes `total_matching` which is the TRUE DB count regardless of
    `limit` (when no tag filter; with tag filter this still reflects the
    pre-tag-filter count). DO NOT use `returned` or `len(items)` as the
    count.

    Args:
        doc_type: Filter by document classification — purchase_order /
            unknown. Empty = all.
        status: Filter by processing status — uploaded / extracting /
            extracted / error. Empty = all.
        filename_like: Substring match on filename (case-insensitive).
            Empty = no filter.
        tag: Exact-match filter — matches if the value appears in EITHER
            the document's `tags` (system + LLM-generated) array OR the
            `user_tags` (manually added in the UI) array.
            - System tags use `key:value` shape: `doc_type:purchase_order`,
              `file_type:pdf`, `has_products:true`, `lang:zh`.
            - LLM topic tags are kebab-case: `celebrity-solstice`,
              `beef-supplier`, `delivery-q2-2026`.
            - User tags are user-typed labels (also kebab-case): `urgent`,
              `重要客户`, `待审`.
            Empty = no tag filter.
        limit: Max rows to return (1-50, default 20).
    """
    deps = get_deps(ctx)
    is_admin = deps.user_role in ("superadmin", "admin")
    bounded = max(1, min(int(limit) if limit else 20, _MAX_LIST_LIMIT))

    # ARCH NOTE: thin wrapper around domain service. Old version wrote SQL
    # directly here and conflated `total = len(items)` (post-tag-filter)
    # with the DB count, leading to wrong "how many docs" answers.
    # See ADR-0002. If you need a new filter, extend the service / repo.
    from domains.document import service as doc_service

    page = doc_service.list_documents(
        db=deps.db,
        user_id=deps.user_id,
        is_admin=is_admin,
        status=status or None,
        doc_type=doc_type or None,
        filename_like=filename_like or None,
        # The service itself only does SQL-level filters here. Tag filter is
        # applied in Python below — see the tag-filter block. We over-fetch
        # when a tag is set so the post-filter set still hits `bounded`.
        limit=bounded * 4 if tag else bounded,
        offset=0,
    )
    rows = page.items
    total_matching = page.total

    # Tag filter (Python-side) — portable across SQLite/Postgres. Match
    # against EITHER `tags` (system+LLM) or `user_tags` (manual).
    # NOTE: when a tag IS set, total_matching reflects the SQL count
    # BEFORE tag filtering. agent's reply for "how many docs with tag X"
    # should use len(items) in that case, not total_matching.
    if tag:
        target = tag.lower()

        def _tagset(d: Any) -> set[str]:
            out: set[str] = set()
            for arr in (
                getattr(d, "tags", None) or [],
                getattr(d, "user_tags", None) or [],
            ):
                for t in arr:
                    if isinstance(t, str):
                        out.add(t.lower())
            return out

        rows = [d for d in rows if target in _tagset(d)][:bounded]

    items = [
        {
            "id": d.id,
            "filename": d.filename,
            "file_type": d.file_type,
            "doc_type": d.doc_type,
            "status": d.status,
            "tags": list(d.tags) if d.tags else [],
            "user_tags": list(d.user_tags) if d.user_tags else [],
            "summary": _truncate(d.summary, _LIST_SUMMARY_PREVIEW_CHARS),
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "extracted_at": d.extracted_at.isoformat() if d.extracted_at else None,
        }
        for d in rows
    ]
    # Field semantics (DO NOT change without updating tool description):
    #   total_matching — exact DB count of rows matching the SQL filters.
    #                    When `tag` is set, the Python tag-filter narrows
    #                    further; `returned` reflects the post-tag count.
    #                    Either way, total_matching ≥ returned.
    #   returned       — rows actually included in `items`.
    #   truncated      — total_matching > returned.
    return json.dumps(
        {
            "total_matching": int(total_matching),
            "returned": len(items),
            "truncated": int(total_matching) > len(items),
            "items": items,
        },
        ensure_ascii=False,
        default=str,
    )


def _truncate(text: str | None, n: int) -> str | None:
    if not text:
        return None
    text = text.strip()
    if len(text) <= n:
        return text
    return text[:n].rstrip() + "…"


@tool(toolset="business", emoji="📑")
def get_document(document_id: int, *, ctx: ToolContext) -> str:
    """Get one document's metadata + a truncated extraction summary.

    `extracted_data` is capped at ~5KB; if larger, `_truncated=true` is
    set and the agent should call `read_document_section` for specific
    pages/sheets instead of asking for the whole thing.

    Args:
        document_id: Numeric document id.
    """
    deps = get_deps(ctx)
    is_admin = deps.user_role in ("superadmin", "admin")
    try:
        detail = doc_service.get_document(
            deps.db, document_id=document_id, user_id=deps.user_id, is_admin=is_admin
        )
    except DocumentError as exc:
        return f"Error: {exc}"

    payload = detail.model_dump()
    extracted = payload.get("extracted_data")
    if isinstance(extracted, (dict, list)):
        as_json = json.dumps(extracted, ensure_ascii=False, default=str)
        if len(as_json) > _MAX_EXTRACTED_DATA_BYTES:
            payload["extracted_data"] = {
                "_truncated": True,
                "_size_bytes": len(as_json),
                "_hint": "Use read_document_section(document_id, page=N) or sheet=...",
                "preview": as_json[:_MAX_EXTRACTED_DATA_BYTES] + "...",
            }
    md = payload.get("content_markdown")
    if isinstance(md, str) and len(md) > _MAX_EXTRACTED_DATA_BYTES:
        payload["content_markdown"] = (
            md[:_MAX_EXTRACTED_DATA_BYTES] + f"... [truncated, total {len(md)} chars]"
        )
    return json.dumps(payload, ensure_ascii=False, default=str)


@tool(toolset="business", emoji="🔎")
def search_documents(
    query: str,
    doc_type: str = "",
    top_k: int = _DEFAULT_SEARCH_TOP_K,
    *,
    ctx: ToolContext,
) -> str:
    """Fuzzy substring search across the user's documents.

    Matches against FOUR fields, in priority order:
      1. `summary`         — the LLM-written abstract (best signal)
      2. `user_tags`       — user-typed labels (e.g. `urgent`, `重要客户`)
      3. `tags`            — system + topic tags (e.g. `celebrity-solstice`)
      4. `content_markdown`— the full extracted text (catches edge cases)

    Returns up to `top_k` hits, each with the matched field, a snippet,
    and the document's tags + user_tags so you can decide whether to
    drill in. For the full content of a hit, follow up with
    `get_document` or `read_document_section`.

    Args:
        query: The text to look for. Case-insensitive.
        doc_type: Optional doc-type filter.
        top_k: Max hits to return (1-20, default 5).
    """
    deps = get_deps(ctx)
    if not query or not query.strip():
        return "Error: `query` must not be empty."
    is_admin = deps.user_role in ("superadmin", "admin")
    bounded = max(1, min(int(top_k) if top_k else _DEFAULT_SEARCH_TOP_K, 20))

    q = query.strip()
    # Split the query into individual terms — a multi-word query like
    # "肉类供应商 新鲜食材" should match either term, not be treated as
    # a literal substring. We split on whitespace and on the common
    # boolean glue words an LLM might emit (`OR`, `or`, `,`).
    terms = [
        t for t in (
            piece.strip()
            for raw in q.replace(",", " ").split()
            for piece in [raw]
            if raw.upper() not in ("OR", "AND")
        )
        if t
    ]
    if not terms:
        terms = [q]

    # ARCH NOTE: SQL filter lives in domain repository (full_text_search),
    # called via service. Snippet extraction stays here because it's the
    # LLM-shaped post-processing — domain doesn't care about snippets.
    from domains.document import service as doc_service

    rows = doc_service.full_text_search(
        deps.db,
        user_id=deps.user_id,
        is_admin=is_admin,
        terms=terms,
        doc_type=doc_type or None,
        limit=bounded,
    )

    hits: list[dict[str, Any]] = []
    for d in rows:
        matched_in, snippet, matched_term = _classify_match_multi(d, terms)
        hit: dict[str, Any] = {
            "document_id": d.id,
            "filename": d.filename,
            "doc_type": d.doc_type,
            "tags": list(d.tags) if d.tags else [],
            "user_tags": list(d.user_tags) if d.user_tags else [],
            "matched_in": matched_in,
            "matched_term": matched_term,
            "snippet": snippet,
        }
        hits.append(hit)
    # NOTE on count semantics: full-text matching over `summary`/`tags`/
    # `content` is done in Python after a bounded SQL fetch, so we can't
    # cheaply compute a true `total_matching` across all docs. We expose
    # `returned` (count of hits in this response) and a `note` warning the
    # caller that this is the count after limit was applied. If the user
    # asks "how many docs mention X" and `returned == limit`, the agent
    # should re-run with a larger limit or use list_documents with tag/
    # filename filters for an exact count.
    return json.dumps(
        {
            "query": q,
            "terms": terms,
            "returned": len(hits),
            "hits": hits,
            "count_note": (
                "returned is post-limit; for exact totals use list_documents"
                if len(hits) >= bounded
                else "all matches returned"
            ),
        },
        ensure_ascii=False,
        default=str,
    )


def _classify_match_multi(d: Document, terms: list[str]) -> tuple[str, str, str]:
    """Find the first matching term and return (field, snippet, term).

    Walks the term list in order; for each term checks
    summary > user_tags > tags > content_markdown so a hit on the
    high-signal fields (the LLM abstract, then human-curated user_tags)
    surfaces before a buried mention in the body text.
    """
    for term in terms:
        tl = term.lower()
        if d.summary and tl in d.summary.lower():
            return "summary", _extract_snippet(d.summary, term), term
        if d.user_tags:
            for t in d.user_tags:
                if tl in t.lower():
                    return "user_tag", t, term
        if d.tags:
            for t in d.tags:
                if tl in t.lower():
                    return "tag", t, term
        if d.content_markdown and tl in d.content_markdown.lower():
            return "content", _extract_snippet(d.content_markdown, term), term
    # SQL matched but our Python re-check didn't pin down the field —
    # surface a summary fallback rather than confusing the agent.
    return (
        "unknown",
        _extract_snippet(d.summary or d.content_markdown or "", terms[0]),
        terms[0] if terms else "",
    )


def _extract_snippet(text: str, query: str) -> str:
    if not text:
        return ""
    lower = text.lower()
    pos = lower.find(query.lower())
    if pos < 0:
        return text[:_MAX_SNIPPET_CHARS]
    half = _MAX_SNIPPET_CHARS // 2
    start = max(0, pos - half)
    end = min(len(text), pos + len(query) + half)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


@tool(toolset="business", emoji="📖")
def read_document_section(
    document_id: int,
    page: int = 0,
    sheet: str = "",
    cell_range: str = "",
    *,
    ctx: ToolContext,
) -> str:
    """Read a specific section of a document.

    For PDFs: pass `page` (1-indexed) to get that page's text.
    For Excels: pass `sheet` (and optionally `cell_range` like "A1:F100")
    to get those cells. Without either, returns sheet/page metadata.

    Args:
        document_id: The document to read.
        page: 1-indexed PDF page number. 0 = no page selected.
        sheet: Excel sheet name. Empty = first sheet.
        cell_range: Excel cell range (e.g. "A1:F100"). Empty = whole sheet.
    """
    deps = get_deps(ctx)
    is_admin = deps.user_role in ("superadmin", "admin")
    try:
        doc = _load_doc(deps.db, document_id, deps.user_id, is_admin)
    except DocumentError as exc:
        return f"Error: {exc}"
    if doc.file_url is None:
        return "Error: document has no file in storage"

    storage = get_storage()
    try:
        blob = storage.download(doc.file_url)
    except Exception as exc:
        return f"Error: could not load file — {exc}"

    if doc.file_type == "pdf":
        return _read_pdf_section(blob, page)
    if doc.file_type == "excel":
        return _read_excel_section(blob, sheet, cell_range)
    return f"Error: read_document_section does not support file_type={doc.file_type}"


def _load_doc(db, document_id: int, user_id: int, is_admin: bool) -> Document:
    """Mirror service-layer ownership check."""
    from domains.document.service import NotFound

    doc = db.get(Document, document_id)
    if doc is None or (not is_admin and doc.user_id != user_id):
        raise NotFound(f"document {document_id} not found")
    return doc


def _read_pdf_section(blob: bytes, page: int) -> str:
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(blob)
        n = len(pdf)
        if page <= 0:
            return json.dumps(
                {"file_type": "pdf", "total_pages": n, "hint": "pass page=N (1-indexed)"},
                ensure_ascii=False,
            )
        if page > n:
            return f"Error: page {page} out of bounds (PDF has {n} pages)"
        text = pdf[page - 1].get_textpage().get_text_range()
        return json.dumps(
            {"page": page, "total_pages": n, "text": text}, ensure_ascii=False
        )
    except Exception as exc:
        return f"Error: PDF read failed — {type(exc).__name__}: {exc}"


def _read_excel_section(blob: bytes, sheet: str, cell_range: str) -> str:
    try:
        from openpyxl import load_workbook

        wb = load_workbook(BytesIO(blob), data_only=True, read_only=True)
        names = wb.sheetnames
        if not sheet:
            return json.dumps(
                {"file_type": "excel", "sheets": names, "hint": "pass sheet=NAME"},
                ensure_ascii=False,
            )
        if sheet not in names:
            return f"Error: sheet '{sheet}' not found (available: {names})"
        ws = wb[sheet]
        if cell_range:
            cells = ws[cell_range]
            # ws[range] returns nested tuples for multi-row, single tuple for one row
            rows: list[list[Any]] = []
            for row in cells:
                if hasattr(row, "value"):
                    rows = [[row.value]]
                    break
                rows.append([c.value for c in row])
        else:
            rows = []
            for row in ws.iter_rows(values_only=True, max_row=200):
                rows.append(list(row))
        return json.dumps(
            {"sheet": sheet, "range": cell_range or "A1:end", "rows": rows},
            ensure_ascii=False,
            default=str,
        )
    except Exception as exc:
        return f"Error: Excel read failed — {type(exc).__name__}: {exc}"
