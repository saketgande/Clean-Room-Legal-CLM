"""A configured OCR provider that does not work must not shadow one that does.

The selection was `databricks if databricks.enabled else reducto` — where
"enabled" means "has credentials", which says nothing about whether it works.
This deployment's Databricks volume answers 403 Forbidden to every upload, so
every scanned PDF landed like this:

    extraction_method  pdf_text_ocr_failed
    quality_score      0.0
    text               '\\n\\n\\n\\n\\n'      <- the whole contract

while a working Reducto key sat two lines away in the same settings object.
The same file through Reducto yields 13,171 characters at quality 0.9.

A scanned contract that silently holds no text is the most expensive failure
in this pipeline: nothing downstream can tell "this contract says nothing"
from "we could not read it", so it is reviewed, scored, advanced to REVIEW and
reported clean.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.contract_files import service


class _Provider:
    def __init__(self, name, *, text=None, raises=None):
        self.provider = name
        self.enabled = True
        self._text = text
        self._raises = raises
        self.calls = 0

    async def extract_text(self, *, filename, mime_type, content):
        self.calls += 1
        if self._raises:
            raise self._raises
        return SimpleNamespace(
            text=self._text or "", quality_score=0.9, provider=self.provider, elements=[]
        )


@pytest.fixture
def scanned(monkeypatch):
    """A PDF whose pages are images: native extraction finds nothing and asks
    for OCR. This is the only path on which provider choice matters."""
    monkeypatch.setattr(
        service,
        "extract_text",
        lambda content, *, mime_type, filename: service.TextExtractionResult(
            text="\n\n\n", method="pdf_text", quality_score=0.0, needs_ocr=True
        ),
    )


def _resolve():
    return asyncio.run(
        service._resolve_extracted_text(
            content=b"%PDF-1.4 scanned", mime_type="application/pdf", filename="scan.pdf"
        )
    )


def _use(monkeypatch, *providers):
    monkeypatch.setattr(service, "_ocr_providers", lambda: list(providers))


# --- the defect --------------------------------------------------------------


def test_a_failing_provider_falls_through_to_a_working_one(monkeypatch, scanned):
    """The failure this file exists for. Databricks 403s; Reducto reads the
    document; the contract must end up with the text, not with three newlines
    and a `needs_review` nobody sees."""
    broken = _Provider("databricks", raises=RuntimeError("Client error '403 Forbidden'"))
    working = _Provider("reducto", text="AMENDMENT NUMBER TWO ...")
    _use(monkeypatch, broken, working)

    result = _resolve()

    assert result.method == "reducto_ocr"
    assert result.text.startswith("AMENDMENT NUMBER TWO")
    assert working.calls == 1


def test_a_provider_returning_no_text_is_also_fallen_through(monkeypatch, scanned):
    """A provider that answers 200 with an empty body has failed just as
    completely as one that raises — and a scanned PDF is exactly the input
    that produces it."""
    empty = _Provider("databricks", text="")
    working = _Provider("reducto", text="AMENDMENT NUMBER TWO ...")
    _use(monkeypatch, empty, working)

    assert _resolve().method == "reducto_ocr"
    assert working.calls == 1


def test_a_working_first_provider_is_not_billed_twice(monkeypatch, scanned):
    """OCR is charged per page. The loop must stop on the first success, not
    poll every configured provider."""
    first = _Provider("databricks", text="AMENDMENT NUMBER TWO ...")
    second = _Provider("reducto", text="should not be reached")
    _use(monkeypatch, first, second)

    assert _resolve().method == "databricks_ocr"
    assert second.calls == 0


def test_every_provider_reason_is_recorded_not_just_the_first(monkeypatch, scanned):
    """An operator looking at an empty contract has to tell "one key is wrong"
    from "this document is genuinely unreadable". Only the full list does
    that."""
    _use(
        monkeypatch,
        _Provider("databricks", raises=RuntimeError("403 Forbidden")),
        _Provider("reducto", raises=RuntimeError("401 Unauthorized")),
    )

    result = _resolve()

    assert result.method == "pdf_text_ocr_failed"
    assert "databricks: " in result.ocr_error
    assert "403 Forbidden" in result.ocr_error
    assert "reducto: " in result.ocr_error
    assert "401 Unauthorized" in result.ocr_error


def test_no_configured_provider_does_not_claim_ocr_failed(monkeypatch, scanned):
    """With nothing configured, OCR did not fail — it never ran. Labelling it
    `_ocr_failed` would send an operator hunting a broken key that does not
    exist."""
    _use(monkeypatch)

    result = _resolve()

    assert result.method == "pdf_text"
    assert result.ocr_error is None
