"""The Word editor in the contract page: ONLYOFFICE Docs.

The editor runs as its own service and never sees a user's session. Three
signed hand-offs connect it to a contract, each checked here:

1. The page asks for a config (an authenticated route in routes.py). It names
   the file by a short-lived link signed with OUR secret, and is itself signed
   with the editor's shared secret so the editor will open it.
2. The editor fetches that link (`/editor/contract-file`) — no session, only the
   signed link, good for one version of one contract for an hour.
3. Save hands the edited file back (`/editor/contract-saved`): believed only if
   the editor signed it, attributed to the person named in OUR signed callback
   link (whose access is checked again now), and fetched only from the editor
   itself, never from an address the callback names. It becomes the contract's
   next version like any upload: read by the Documents reader, AI jobs queued.

A Word original opens as itself. A PDF or generated contract opens as the Word
export of its current text (headings and numbering kept), since the free editor
edits Word, not PDF.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
import jwt
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from app.contract_files.models import ContractTextSnapshot, ContractVersion, StorageObject
from app.core.config import settings
from app.core.database import utcnow
from app.core.deps import get_db
from app.integrations.storage import storage_service

log = logging.getLogger(__name__)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_FILE_TTL = timedelta(hours=1)
# The callback can come long after opening: the editor saves when the last
# person closes it, and Save can be pressed any time while it is open.
_SAVE_TTL = timedelta(hours=12)
_MAX_BYTES = 50 * 1024 * 1024

router = APIRouter(prefix="/editor", tags=["editor"], include_in_schema=False)


def enabled() -> bool:
    return bool(settings.onlyoffice_url and settings.onlyoffice_jwt_secret)


def _sign(claims: dict, ttl: timedelta) -> str:
    return jwt.encode({**claims, "exp": utcnow() + ttl}, settings.secret_key, algorithm="HS256")


def _verify(token: str, purpose: str) -> dict:
    """Our own link token. `typ` keeps it from ever passing as a login token
    (those are typ=access) and keeps a file link from working as a save link."""
    try:
        claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Link expired or not ours.") from None
    if claims.get("typ") != purpose:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Wrong kind of link.")
    return claims


def _file_for(db: Session, contract, version: ContractVersion) -> tuple[bytes, bool]:
    """The Word file to open, and whether it is the original upload."""
    from app.contract_files.routes import _build_plain_docx, _build_structured_docx

    stored = db.get(StorageObject, version.storage_object_id) if version.storage_object_id else None
    if stored is not None and stored.mime_type == DOCX:
        return storage_service.read_bytes(stored.storage_key), True
    snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id) if version.text_snapshot_id else None
    if snapshot is None or not (snapshot.text or "").strip():
        raise HTTPException(status.HTTP_409_CONFLICT, "This version has no text to open.")
    # No title or version line: whatever the editor opens becomes the contract's
    # text on save, and a heading added here would be saved into it.
    if snapshot.structure_status == "structured" and snapshot.element_count:
        return _build_structured_docx(db, snapshot, title="", subtitle=""), False
    return _build_plain_docx(title="", subtitle="", text=snapshot.text), False


def build_config(db: Session, *, contract, user, can_edit: bool) -> dict:
    """What the page hands the editor to open the contract's current version."""
    if not enabled():
        return {"enabled": False}
    version = db.get(ContractVersion, contract.current_authoritative_version_id) \
        if contract.current_authoritative_version_id else None
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This contract has no version to open.")
    stored = db.get(StorageObject, version.storage_object_id) if version.storage_object_id else None
    original = stored is not None and stored.mime_type == DOCX
    # Suggested changes still waiting are anchored to this version's text. A
    # Word save would make a new version and strand them (Accept then refuses),
    # so the editor opens read-only until they are decided.
    pending = _pending_redlines(db, contract.id)
    read_only_reason = None
    if can_edit and pending:
        can_edit = False
        read_only_reason = (f"{pending} suggested change{'s are' if pending != 1 else ' is'} waiting. "
                            "Accept or reject them first, then edit in Word.")
    base = settings.onlyoffice_callback_base_url.rstrip("/") + settings.api_v1_prefix + "/editor"
    file_token = _sign({"typ": "editor_file", "cid": contract.id, "vid": version.id}, _FILE_TTL)
    save_token = _sign({"typ": "editor_save", "cid": contract.id, "vid": version.id, "uid": user.id}, _SAVE_TTL)
    config = {
        "document": {
            "fileType": "docx",
            # A new version is a new key, so the editor never serves a stale copy.
            # Everyone opening the same version shares the key, so they co-edit.
            "key": f"{version.id}{'' if original else '-text'}",
            "title": f"{contract.title[:80]} (v{version.version_number}).docx",
            "url": f"{base}/contract-file?token={file_token}",
            "permissions": {"edit": can_edit, "review": can_edit, "comment": can_edit,
                            "download": True, "print": True},
        },
        "documentType": "word",
        "editorConfig": {
            "mode": "edit" if can_edit else "view",
            "lang": "en",
            "callbackUrl": f"{base}/contract-saved?token={save_token}",
            "user": {"id": user.id, "name": user.full_name or user.email},
            # Save writes a version at once; every edit is a tracked change by
            # its author, so the next reviewer (or the counterparty) sees it.
            "customization": {"forcesave": True, "compactHeader": True, "review": {"trackChanges": True}},
        },
        "width": "100%",
        "height": "100%",
        "type": "desktop",
    }
    config["token"] = jwt.encode(config, settings.onlyoffice_jwt_secret, algorithm="HS256")
    return {
        "enabled": True,
        "server": settings.onlyoffice_url.rstrip("/"),
        "config": config,
        "version_number": version.version_number,
        "original": original,
        "read_only_reason": read_only_reason,
    }


def _pending_redlines(db: Session, contract_id: str) -> int:
    from sqlalchemy import func, select

    from app.contract_files.models import ContractEdit

    return db.scalar(select(func.count(ContractEdit.id)).where(
        ContractEdit.contract_id == contract_id, ContractEdit.status == "proposed")) or 0


def words_of(docx: bytes) -> list[str]:
    """A Word file's words as the Documents reader reads them (tracked
    insertions in, deletions out) — what a save is compared on."""
    from app.contract_files.structure import read_with_documents_reader
    from app.contract_files.text_extraction import extract_text

    read = read_with_documents_reader(docx, mime_type=DOCX, filename="edited.docx")
    text = read["text"] if read else extract_text(docx, mime_type=DOCX, filename="edited.docx").text
    return text.split()


@router.get("/contract-file")
def contract_file(token: str, db: Session = Depends(get_db)) -> Response:
    """The file the editor opens. No session: the signed link is the permission."""
    from app.contracts.models import Contract

    claims = _verify(token, "editor_file")
    contract = db.get(Contract, claims["cid"])
    version = db.get(ContractVersion, claims["vid"])
    if contract is None or version is None or version.contract_id != contract.id or contract.deleted_at:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    content, _ = _file_for(db, contract, version)
    return Response(content, media_type=DOCX)


def same_document(a: bytes, b: bytes) -> bool:
    """A Word file saved twice differs only in its zip's timestamps: compare parts."""
    if a == b:
        return True
    try:
        x, y = zipfile.ZipFile(io.BytesIO(a)), zipfile.ZipFile(io.BytesIO(b))
        return sorted(x.namelist()) == sorted(y.namelist()) and all(x.read(n) == y.read(n) for n in x.namelist())
    except zipfile.BadZipFile:
        return False


@router.post("/contract-saved")
async def contract_saved(
    token: str,
    body: dict = Body(...),
    authorization: str = Header(""),
    db: Session = Depends(get_db),
) -> dict:
    """The editor handing back an edited file (status 2: closed with changes;
    6: Save pressed). Answers {"error": 0} when handled; anything else makes
    the editor keep the changes and try again."""
    from app.auth.models import User
    from app.contract_files.service import add_version_from_upload
    from app.contracts.service import get_contract_for_user
    from app.core.audit import write_timeline_event
    from app.core.enums import ContractVersionSource, UserStatus
    from app.core.rbac import has_permission

    signed = body.get("token") or authorization.removeprefix("Bearer ").strip()
    try:
        editor_claims = jwt.decode(signed, settings.onlyoffice_jwt_secret or "", algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not signed by the editor.") from None
    editor_claims = editor_claims.get("payload", editor_claims)  # a header token wraps the body
    ours = _verify(token, "editor_save")
    if editor_claims.get("status") not in (2, 6):
        return {"error": 0}

    user = db.get(User, ours["uid"])
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The person who opened the editor can't save.")
    contract = get_contract_for_user(db, contract_id=ours["cid"], user=user)  # access, checked again now
    if not has_permission(user.permission_values, "contract_file:update"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No permission to add versions.")

    # Fetched from the editor itself: the callback names a file on the editor,
    # and only its path is used, so it can't point this server anywhere else.
    where = urlsplit(str(editor_claims.get("url") or ""))
    source = settings.onlyoffice_internal_url.rstrip("/") + where.path + (f"?{where.query}" if where.query else "")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            answer = await client.get(source)
        answer.raise_for_status()
    except httpx.HTTPError:
        log.warning("editor save: fetching the edited file for %s failed", contract.id, exc_info=True)
        return {"error": 1}
    content = answer.content
    if len(content) > _MAX_BYTES or not content.startswith(b"PK"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The editor sent back something that isn't Word.")

    opened = db.get(ContractVersion, ours["vid"])
    current = db.get(ContractVersion, contract.current_authoritative_version_id)
    # Nothing actually changed: the words match the file the editor opened.
    # ONLYOFFICE reports a generated file (a PDF's or a draft's Word export) as
    # changed on close, and a byte compare can't see through that — it made a
    # version whose only difference was the export's title line, stranding
    # every suggested change on the version before it.
    if opened is not None:
        served, _ = _file_for(db, contract, opened)
        if words_of(content) == words_of(served):
            return {"error": 0}
    # The editor sends the file again when it closes, even with nothing new
    # since Save: that is not a version.
    if current is not None and current.storage_object_id:
        stored = db.get(StorageObject, current.storage_object_id)
        if stored is not None and stored.mime_type == DOCX and \
                same_document(content, storage_service.read_bytes(stored.storage_key)):
            return {"error": 0}
    who = user.full_name or user.email
    summary = f"Edited in Word by {who}"
    if current is not None and opened is not None and current.id != opened.id:
        # ponytail: a newer version arrived while the editor was open, and this
        # save is based on the older one. Kept (never drop someone's edits) and
        # flagged in its summary; a merge would need a three-way compare.
        summary += f" (from v{opened.version_number}; v{current.version_number} existed)"
    upload = UploadFile(io.BytesIO(content), filename=f"{contract.title[:60]}-edited.docx",
                        headers=Headers({"content-type": DOCX}))
    version = await add_version_from_upload(db, contract=contract, upload=upload, user=user,
                                            change_summary=summary[:240], source=ContractVersionSource.MANUAL_EDIT)
    write_timeline_event(
        db, org_id=contract.org_id, resource_type="contract", resource_id=contract.id,
        event_type="contract.edited_in_word", title=summary[:240], actor_user_id=user.id,
        details={"contract_version_id": version.id, "from_version_id": ours["vid"],
                 "saved": "Save pressed" if editor_claims["status"] == 6 else "editor closed"},
    )
    db.commit()
    return {"error": 0}
