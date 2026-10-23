"""A contract the system drafted must not say "Uploaded document" in its stage history.

A request's draft goes through the same intake path as an attached file (it is stored as a text upload), so
the automatic move to Review was always worded "Uploaded document — moved to review automatically". That is
true for a file someone attached and wrong for a draft the system wrote from the template.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.contract_files.service import ContractFilesService
from app.contracts import lifecycle
from app.intake import drafting


def _move(monkeypatch, **extra):
    seen: dict = {}
    monkeypatch.setattr(lifecycle, "transition_contract_stage", lambda db, **kw: seen.update(kw))
    service = ContractFilesService(MagicMock())
    service._advance_upload_to_review(
        contract=SimpleNamespace(id="c-1"), user=SimpleNamespace(id="u-1"), request_id=None, **extra
    )
    return seen


def test_an_attached_file_still_says_it_was_uploaded(monkeypatch):
    assert _move(monkeypatch)["reason"] == "Uploaded document — moved to review automatically"


def test_a_given_reason_is_what_the_history_says(monkeypatch):
    assert _move(monkeypatch, reason=drafting.DRAFTED_MOVE_REASON)["reason"] == (
        "Drafted from the template — moved to review automatically"
    )


def test_the_template_draft_gives_its_own_reason_and_the_attachment_path_keeps_the_default():
    drafted = inspect.getsource(drafting.DraftingService.draft_contract_for_request)
    attached = inspect.getsource(drafting.DraftingService.ingest_attachment_as_contract)
    assert "advance_reason=DRAFTED_MOVE_REASON" in drafted
    assert "advance_reason" not in attached
