"""A request attachment that needs OCR is OCR'd before filing (REQ-10382: a scan whose only text was
DocuSign stamps became a 874-byte stamp-only contract, because only emailed attachments were OCR'd)."""
from types import SimpleNamespace

from app.contract_files.text_extraction import TextExtractionResult
from app.intake import service


def _patch(monkeypatch, needs_ocr, ocr_text):
    monkeypatch.setattr("app.contract_files.service.validate_upload_mime", lambda c, m: m)
    monkeypatch.setattr("app.contract_files.service._scan_for_malware", lambda c: None)
    monkeypatch.setattr("app.contract_files.text_extraction.extract_text", lambda *a, **k: TextExtractionResult(
        "DocuSign Envelope ID: X " * 10, "pdf_text", 0.76, needs_ocr=needs_ocr))

    class Reducto:
        async def extract_text(self, **k):
            if isinstance(ocr_text, Exception):
                raise ocr_text
            return SimpleNamespace(text=ocr_text)

    monkeypatch.setattr("app.integrations.dependencies.get_reducto_client", lambda: Reducto())


def test_scan_uses_ocr_text(monkeypatch):
    _patch(monkeypatch, True, "This Master Services Agreement is made between the parties. " * 5)
    att = service._prepare_attachment("a.pdf", "application/pdf", b"%PDF")
    assert att.extracted_text.startswith("This Master Services Agreement")


def test_text_pdf_is_not_ocrd(monkeypatch):
    _patch(monkeypatch, False, "should not be used")
    assert service._prepare_attachment("a.pdf", "application/pdf", b"%PDF").extracted_text.startswith("DocuSign")


def test_failed_ocr_keeps_native_text(monkeypatch):
    _patch(monkeypatch, True, RuntimeError("down"))
    assert service._prepare_attachment("a.pdf", "application/pdf", b"%PDF").extracted_text.startswith("DocuSign")
