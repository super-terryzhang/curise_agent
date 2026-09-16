"""Artifact data endpoints — `/api/artifacts/*`.

These endpoints feed the components registered in `agent/runtime/artifacts.py`.
The agent calls `present_artifact(component, data, narration)`, which
emits an SSE event carrying `component` + a tiny reference dict (e.g.
`batch_id=12`). The frontend uses those to hit the matching endpoint
here and pull the full data needed to render.

Why a separate router rather than reusing existing endpoints:
    - One stable URL space for the artifact catalog (`/api/artifacts/...`).
      Future components add an entry here without negotiating with
      domain-owners about endpoint shape.
    - The shape is the artifact contract — separate from `preview_upload`
      tool output (which is for the LLM to read). Coupling them would
      force one to bend for the other.
    - Cross-user isolation lives in one place — every endpoint here
      enforces `current_user` on the underlying resource owner.

Adding a new component:
    1. Register in `agent/runtime/artifacts.py` with the data shape
       the agent will pass.
    2. Add a `GET /api/artifacts/{component-slug}/{ref}` endpoint here.
    3. Add a React component in `v3-frontend/src/components/artifacts/`.
    4. No agent prompt changes — the tool docstring auto-loads from
       the catalog.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from apps.http._deps import CurrentUser, DbDep
from domains.inquiry import service as inquiry_service
from domains.masterdata.upload import preview_changes
from domains.masterdata.upload.errors import (
    BatchInWrongState,
    BatchNotFound,
    BatchOwnedByOther,
)
from domains.orders.models import Order

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/upload-diff/{batch_id}")
def get_upload_diff_artifact(
    batch_id: int,
    db: DbDep,
    user: CurrentUser,
    limit: int = 200,
) -> dict:
    """Full 4-state diff data for the `upload_diff_viewer` component.

    The frontend hits this once when it receives an `artifact` SSE
    event with `component="upload_diff_viewer"` and `data.batch_id=N`.
    Body shape mirrors `preview_changes()` output verbatim — the React
    component knows how to consume it; we don't reshape here.

    Cross-user safety: 404 if the batch doesn't belong to the
    requesting user (BatchOwnedByOther → 404, not 403, to avoid
    confirming existence to non-owners).

    Args:
        batch_id: Numeric id of the upload batch.
        limit: Max rows per group. Default 200 — higher than the
            agent-tool default (50) because the viewer will paginate
            client-side.
    """
    # Cap to a sane upper bound — preventing a malicious client from
    # asking for a million rows
    bounded_limit = max(1, min(int(limit), 500))

    try:
        out = preview_changes(
            db, batch_id=batch_id, user_id=user.id, limit=bounded_limit
        )
    except BatchNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except BatchOwnedByOther as exc:
        # Use 404 — never confirm existence to non-owners.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"batch {batch_id} not found",
        ) from exc
    except BatchInWrongState as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    return out


@router.get("/inquiry-batch/{order_id}")
def get_inquiry_batch_artifact(
    order_id: int,
    db: DbDep,
    user: CurrentUser,
) -> dict:
    """Live snapshot for the `inquiry_batch_card` artifact.

    Renders the file list (one entry per supplier the inquiry job
    produced) with per-file download URLs and status. Reads the
    persisted inquiry state at request time — so a regenerate or partial
    failure correctly shows the *current* set of files, not whatever
    was true the moment the agent dispatched the artifact.

    Returns 404 (never 403) if the order doesn't belong to the caller —
    keeps order-existence opaque to non-owners.

    Body shape (must match what InquiryBatchCard.tsx consumes):
        {
            "order_id": int,
            "po_number": str | None,
            "status": "pending" | "running" | "succeeded" | "failed" | ...,
            "supplier_count": int,
            "files": [
                {
                    "supplier_id": int,
                    "supplier_name": str,
                    "filename": str | None,
                    "download_url": str | None,
                    "status": str,
                    "error_message": str | None,
                },
                ...
            ],
        }
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if order is None or order.user_id != user.id:
        # Use 404 — never confirm existence to non-owners.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"order {order_id} not found",
        )

    state = inquiry_service.read_inquiry_state(db, order_id)
    if state is None:
        # Order exists but no inquiry has been kicked off. Return an
        # empty-but-shaped body so the frontend can render "no files
        # yet" instead of erroring out.
        return {
            "order_id": order_id,
            "po_number": order.po_number,
            "status": "no_inquiry",
            "supplier_count": 0,
            "files": [],
        }

    files: list[dict] = []
    for s in state.suppliers:
        # Surface only suppliers that produced something or failed in a
        # user-visible way. Pending rows are hidden — they belong in the
        # progress view, not the artifact card.
        filename: str | None = None
        if s.excel_file_url:
            # Convention from inquiry orchestrator: file URLs end with
            # the filename component. Tolerate both absolute URLs and
            # relative `/files/<name>` paths.
            filename = s.excel_file_url.rsplit("/", 1)[-1] or None
        files.append({
            "supplier_id": s.supplier_id,
            "supplier_name": s.supplier_name or f"Supplier #{s.supplier_id}",
            "filename": filename,
            "download_url": s.excel_file_url,
            "status": s.status,
            "error_message": s.error_message,
        })

    return {
        "order_id": order_id,
        "po_number": order.po_number,
        "status": state.status,
        "supplier_count": state.supplier_count,
        "files": files,
    }
