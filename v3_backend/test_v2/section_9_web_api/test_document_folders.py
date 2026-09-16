"""Section 9 — Web API: document folders (P3B 2026-06-21).

Tests cover the full lifecycle:
    create → list → rename → re-parent → move docs → delete
plus the integrity invariants enforced in service:
    - cross-user isolation (404 not 403)
    - sibling-name uniqueness
    - delete refuses on non-empty folder
    - cycle prevention on re-parent
    - depth cap
    - "?folder_id=root" filter shows only unfiled docs
    - documents respond with folder_id field
"""

from __future__ import annotations

from domains.document.models import Document, DocumentFolder
from test_v2.fixtures.helpers import login, make_minimal_pdf, seed_user


def _upload(client, headers, filename="t.pdf") -> dict:
    pdf = make_minimal_pdf("hi")
    r = client.post(
        "/api/documents/upload",
        files={"file": (filename, pdf, "application/pdf")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _create_folder(client, headers, name, parent_folder_id=None) -> dict:
    r = client.post(
        "/api/document-folders",
        json={"name": name, "parent_folder_id": parent_folder_id},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


# ─── CRUD basics ──────────────────────────────────────────────


def test_create_lists_and_returns_folder(client, db):
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    folder = _create_folder(client, headers, "Celebrity 2026-06")
    assert folder["name"] == "Celebrity 2026-06"
    assert folder["parent_folder_id"] is None
    assert folder["document_count"] == 0

    r = client.get("/api/document-folders", headers=headers)
    assert r.status_code == 200
    folders = r.json()
    assert any(f["id"] == folder["id"] for f in folders)


def test_create_rejects_empty_name(client, db):
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    r = client.post(
        "/api/document-folders", json={"name": "   "}, headers=headers
    )
    assert r.status_code == 400


def test_create_rejects_duplicate_sibling_name(client, db):
    """Two folders called "X" under the same parent are confusing — block
    them at the API layer rather than letting the user pick a winner
    later."""
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    _create_folder(client, headers, "X")
    r = client.post(
        "/api/document-folders", json={"name": "X"}, headers=headers
    )
    assert r.status_code == 400
    assert "同级已存在" in r.json()["detail"]


def test_rename_folder_persists(client, db, session_factory):
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    folder = _create_folder(client, headers, "Old")
    r = client.patch(
        f"/api/document-folders/{folder['id']}",
        json={"name": "New"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "New"

    fresh = session_factory()
    try:
        f = fresh.get(DocumentFolder, folder["id"])
        assert f.name == "New"
    finally:
        fresh.close()


# ─── Hierarchy ────────────────────────────────────────────────


def test_nested_folder_creation_and_reparent(client, db):
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    parent = _create_folder(client, headers, "Cruises")
    child = _create_folder(
        client, headers, "Celebrity", parent_folder_id=parent["id"]
    )
    assert child["parent_folder_id"] == parent["id"]

    # Re-parent child to root.
    r = client.patch(
        f"/api/document-folders/{child['id']}",
        json={"parent_folder_id": None},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["parent_folder_id"] is None


def test_reparent_into_own_descendant_is_blocked(client, db):
    """Folder A -> B -> C, then trying to move A under C should fail —
    that would create a cycle A -> B -> C -> A."""
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    a = _create_folder(client, headers, "A")
    b = _create_folder(client, headers, "B", parent_folder_id=a["id"])
    c = _create_folder(client, headers, "C", parent_folder_id=b["id"])

    r = client.patch(
        f"/api/document-folders/{a['id']}",
        json={"parent_folder_id": c["id"]},
        headers=headers,
    )
    assert r.status_code == 400
    assert "成环" in r.json()["detail"]


def test_self_parent_is_blocked(client, db):
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    f = _create_folder(client, headers, "X")
    r = client.patch(
        f"/api/document-folders/{f['id']}",
        json={"parent_folder_id": f["id"]},
        headers=headers,
    )
    assert r.status_code == 400


# ─── Delete safety ────────────────────────────────────────────


def test_delete_refuses_when_folder_has_documents(client, db):
    """Forces explicit relocation. Silent cascade would lose user work."""
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    folder = _create_folder(client, headers, "Trash candidate")
    doc = _upload(client, headers)
    r = client.patch(
        f"/api/documents/{doc['id']}/folder",
        json={"folder_id": folder["id"]},
        headers=headers,
    )
    assert r.status_code == 200

    r = client.delete(
        f"/api/document-folders/{folder['id']}", headers=headers
    )
    assert r.status_code == 400
    assert "仍有文档" in r.json()["detail"]


def test_delete_refuses_when_folder_has_subfolders(client, db):
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    parent = _create_folder(client, headers, "Parent")
    _create_folder(client, headers, "Child", parent_folder_id=parent["id"])
    r = client.delete(
        f"/api/document-folders/{parent['id']}", headers=headers
    )
    assert r.status_code == 400
    assert "子文件夹" in r.json()["detail"]


def test_delete_empty_folder_succeeds(client, db, session_factory):
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    folder = _create_folder(client, headers, "Empty")
    r = client.delete(
        f"/api/document-folders/{folder['id']}", headers=headers
    )
    assert r.status_code == 204

    fresh = session_factory()
    try:
        assert fresh.get(DocumentFolder, folder["id"]) is None
    finally:
        fresh.close()


# ─── Move document ────────────────────────────────────────────


def test_move_document_into_and_out_of_folder(client, db, session_factory):
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    folder = _create_folder(client, headers, "F")
    doc = _upload(client, headers)

    # Into folder
    r = client.patch(
        f"/api/documents/{doc['id']}/folder",
        json={"folder_id": folder["id"]},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["folder_id"] == folder["id"]

    # Back to root
    r = client.patch(
        f"/api/documents/{doc['id']}/folder",
        json={"folder_id": None},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["folder_id"] is None


def test_list_documents_filter_by_folder(client, db):
    """`?folder_id=<id>` returns docs in that folder; `?folder_id=root`
    returns only unfiled docs; absent param returns everything."""
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    folder = _create_folder(client, headers, "Inbox")
    a = _upload(client, headers, filename="a.pdf")
    b = _upload(client, headers, filename="b.pdf")
    client.patch(
        f"/api/documents/{a['id']}/folder",
        json={"folder_id": folder["id"]},
        headers=headers,
    )

    # Inside folder
    r = client.get(
        f"/api/documents?folder_id={folder['id']}", headers=headers
    )
    ids = [d["id"] for d in r.json()["items"]]
    assert a["id"] in ids and b["id"] not in ids

    # Root only
    r = client.get("/api/documents?folder_id=root", headers=headers)
    ids = [d["id"] for d in r.json()["items"]]
    assert b["id"] in ids and a["id"] not in ids


# ─── Cross-user isolation ─────────────────────────────────────


def test_folders_are_company_wide_across_users(client, db):
    """2026-07-03 policy flip: folders + documents are company-wide,
    not per-user. Alice CAN see + rename Bob's folder — that's the
    intended behavior. The only owner check that survives is
    `delete_folder` (elsewhere in this file / regression suite).

    This test locks in the new contract; if a future refactor
    re-adds per-user filtering to list/update, this fails immediately.
    """
    seed_user(db, email="alice@x.test", role="employee")
    seed_user(db, email="bob@x.test", role="employee")
    alice_headers = login(client, "alice@x.test")
    bob_headers = login(client, "bob@x.test")

    bobs = _create_folder(client, bob_headers, "Bobs Folder")

    # Alice sees Bob's folder in her list.
    r = client.get("/api/document-folders", headers=alice_headers)
    assert r.status_code == 200
    names = [f["name"] for f in r.json()]
    assert "Bobs Folder" in names

    # Alice can rename it.
    r = client.patch(
        f"/api/document-folders/{bobs['id']}",
        json={"name": "Renamed by Alice"},
        headers=alice_headers,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed by Alice"


# ─── Response contains folder_id on documents ─────────────────


def test_get_document_includes_folder_id_field(client, db):
    """The frontend expects the document detail/list payloads to carry
    `folder_id` — without it, the move dropdown can't show the current
    selection."""
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    doc = _upload(client, headers)
    r = client.get(f"/api/documents/{doc['id']}", headers=headers)
    assert r.status_code == 200
    assert "folder_id" in r.json()
    assert r.json()["folder_id"] is None


# ─── 2026-07-03 policy lock-in tests ─────────────────────────
#
# Cross-user integration ops (move, rename) are now allowed —
# documents + folders are company-wide assets. But destructive /
# expensive ops (delete, doc_type change, reextract) still gate on
# uploader + admin. These three tests pin the split so a future
# refactor can't quietly drift back to per-user or fully-open.


def test_employee_can_move_another_employees_document(client, db):
    """The bug that triggered this whole change (2026-07-03 Felix):
    admin couldn't move an employee's upload into an admin folder.
    Now ANY employee can move ANY document into ANY folder — the
    document + folder both stay company-wide.
    """
    seed_user(db, email="alice@x.test", role="employee")
    seed_user(db, email="bob@x.test", role="employee")
    alice_headers = login(client, "alice@x.test")
    bob_headers = login(client, "bob@x.test")

    # Bob uploads a doc. Alice creates a folder. Alice moves Bob's doc
    # into her folder — succeeds.
    bobs_doc = _upload(client, bob_headers, filename="bobs.pdf")
    alices_folder = _create_folder(client, alice_headers, "Alices Archive")

    r = client.patch(
        f"/api/documents/{bobs_doc['id']}/folder",
        json={"folder_id": alices_folder["id"]},
        headers=alice_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["folder_id"] == alices_folder["id"]


def test_employee_cannot_delete_another_employees_document(client, db):
    """Destructive ops (delete) still owner-gated: Alice cannot delete
    Bob's upload. This is the safety net protecting against peers
    wiping each other's work — 404 (not 403) so we don't leak that
    the doc exists but is off-limits."""
    seed_user(db, email="alice@x.test", role="employee")
    seed_user(db, email="bob@x.test", role="employee")
    alice_headers = login(client, "alice@x.test")
    bob_headers = login(client, "bob@x.test")

    bobs_doc = _upload(client, bob_headers, filename="bobs.pdf")
    r = client.delete(
        f"/api/documents/{bobs_doc['id']}", headers=alice_headers
    )
    assert r.status_code == 404, r.text


def test_employee_cannot_change_another_employees_doc_type(client, db):
    """`update_doc_type` triggers Gemini enrichment when switching TO
    `purchase_order` — that's a real API-cost, so we gate it by
    uploader too, not just delete-level ops. Alice must not be able to
    silently spend Bob's Gemini quota."""
    seed_user(db, email="alice@x.test", role="employee")
    seed_user(db, email="bob@x.test", role="employee")
    alice_headers = login(client, "alice@x.test")
    bob_headers = login(client, "bob@x.test")

    bobs_doc = _upload(client, bob_headers, filename="bobs.pdf")
    r = client.patch(
        f"/api/documents/{bobs_doc['id']}",
        json={"doc_type": "purchase_order"},
        headers=alice_headers,
    )
    assert r.status_code == 404, r.text


# ─── Color (2026-07-22) ───────────────────────────────────────


def test_create_folder_accepts_swatch_color(client, db):
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    r = client.post(
        "/api/document-folders",
        json={"name": "Blue folder", "color": "#3b82f6"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["color"] == "#3b82f6"


def test_create_folder_rejects_bad_color(client, db):
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    r = client.post(
        "/api/document-folders",
        json={"name": "junk color", "color": "not-a-color"},
        headers=headers,
    )
    assert r.status_code == 400


def test_patch_color_persists_and_returns_in_list(client, db):
    """The point of storing color server-side is that every viewer sees
    the same color — verify PATCH round-trips through /list."""
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    folder = _create_folder(client, headers, "Colored")

    r = client.patch(
        f"/api/document-folders/{folder['id']}",
        json={"color": "#22c55e"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["color"] == "#22c55e"

    # List must also reflect the new color.
    r2 = client.get("/api/document-folders", headers=headers)
    assert r2.status_code == 200
    stored = next(f for f in r2.json() if f["id"] == folder["id"])
    assert stored["color"] == "#22c55e"


def test_patch_color_null_clears_back_to_default(client, db):
    """Setting color=null explicitly is how the UI reverts a folder to the
    palette default. Absent field vs null-field disambiguation must work
    end-to-end via `color_set`."""
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    folder = _create_folder(client, headers, "TempColor")
    client.patch(
        f"/api/document-folders/{folder['id']}",
        json={"color": "#ef4444"},
        headers=headers,
    )
    r = client.patch(
        f"/api/document-folders/{folder['id']}",
        json={"color": None},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["color"] is None


def test_patch_name_does_not_clobber_color(client, db):
    """Rename-only PATCH must leave `color` alone — this is the whole
    point of the parent_set / color_set sentinel plumbing."""
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    folder = _create_folder(client, headers, "KeepColor")
    client.patch(
        f"/api/document-folders/{folder['id']}",
        json={"color": "#8b5cf6"},
        headers=headers,
    )
    r = client.patch(
        f"/api/document-folders/{folder['id']}",
        json={"name": "Renamed"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Renamed"
    assert body["color"] == "#8b5cf6"
