"""Oracle scan status and explicit, authenticated manual trigger."""

from fastapi import APIRouter, HTTPException

from apps.http._deps import CurrentUser, DbDep, Writer
from apps.jobs.oracle_scan import request_po_import, request_scan, scan_status

router = APIRouter(prefix="/oracle", tags=["oracle"])


@router.get("/scans")
def get_scans(db: DbDep, user: CurrentUser):
    return scan_status(db)


@router.post("/scans", status_code=202)
def start_scan(db: DbDep, user: Writer):
    try:
        return request_scan(db, user.id)
    except ValueError as error:
        raise HTTPException(503, "自动收单尚未启用，请联系管理员") from error


@router.post("/imports/{po_number}", status_code=202)
def start_import(po_number: str, db: DbDep, user: Writer):
    try:
        return request_po_import(db, user.id, po_number)
    except LookupError as error:
        raise HTTPException(409, "该 PO 当前不在待确认导入列表，请刷新记录") from error
    except RuntimeError as error:
        raise HTTPException(409, "已有扫描或导入正在执行，请完成后再导入") from error
    except ValueError as error:
        raise HTTPException(503, "自动收单尚未启用，请联系管理员") from error
