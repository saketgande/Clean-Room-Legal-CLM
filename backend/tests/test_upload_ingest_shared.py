"""FILE-02 / FILE-03: every upload endpoint goes through one hardened read (streamed
size limit, type check, antivirus scan), and the playbook builder reports each file
it couldn't read instead of counting it as an empty document."""

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.contract_files import service
from app.core.config import settings
from app.notices import routes as notices_routes
from app.playbooks import routes as playbook_routes
from app.trademarks import service as trademarks_service


class FakeUpload:
    def __init__(self, chunks, filename="doc.txt", content_type="text/plain"):
        self.chunks, self.filename, self.content_type, self.reads = list(chunks), filename, content_type, 0

    async def read(self, _size=-1):
        self.reads += 1
        return self.chunks.pop(0) if self.chunks else b""


def test_an_oversized_upload_is_cut_off_while_streaming(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_bytes", 10)
    monkeypatch.setattr(settings, "upload_stream_chunk_bytes", 4)
    upload = FakeUpload([b"aaaa"] * 1000)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.ingest_upload(upload))
    assert exc.value.status_code == 413 and upload.reads == 3


def test_every_upload_is_scanned(monkeypatch):
    scanned = []
    monkeypatch.setattr(service, "_scan_for_malware", lambda content: scanned.append(content))
    ingested = asyncio.run(service.ingest_upload(FakeUpload([b"Notice of breach."], filename="notice.txt")))
    assert scanned == [b"Notice of breach."] and ingested.mime_type == "text/plain"


def test_every_upload_endpoint_uses_the_shared_read():
    # Contract-file and trademark uploads are service methods since the DI
    # refactor; the module-level names are wrappers that only delegate.
    for fn in (service.ContractFilesService.create_contract_from_upload,
               service.ContractFilesService.add_version_from_upload,
               playbook_routes._resolve_source_text, playbook_routes.build_extract_documents,
               notices_routes.extract_from_document, notices_routes.add_document,
               trademarks_service.TrademarksService.save_uploaded_document):
        source = inspect.getsource(fn)
        assert "ingest_upload(" in source, fn.__name__
        assert "await file.read()" not in source and "await upload.read()" not in source, fn.__name__


def test_the_builder_flags_a_file_it_could_not_read(monkeypatch):
    async def ingest(file):
        return service.IngestedUpload(filename=file.filename, mime_type="application/pdf", content=b"%PDF-")

    async def extract(*, content, mime_type, filename):
        return SimpleNamespace(text="Liability is capped at fees paid." if filename == "msa.pdf" else "")

    monkeypatch.setattr(playbook_routes, "ingest_upload", ingest)
    monkeypatch.setattr(playbook_routes, "_resolve_extracted_text", extract)
    files = [SimpleNamespace(filename="msa.pdf"), SimpleNamespace(filename="scan.pdf")]
    docs = asyncio.run(playbook_routes.build_extract_documents(files=files, db=None, current_user=None))
    assert [(d["filename"], d["status"]) for d in docs] == [("msa.pdf", "ok"), ("scan.pdf", "unreadable")]
    assert docs[1]["reason"] and docs[1]["chars"] == 0
