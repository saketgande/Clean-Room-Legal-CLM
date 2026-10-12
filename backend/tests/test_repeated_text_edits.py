"""APP-05: an edit to text that appears more than once lands on the occurrence the
reader chose, or is refused; it is never silently applied to the first one."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai.tool_runtime import _anchor_suggestions
from app.contract_files import routes
from app.contract_files.blocks import anchor_quote, split_blocks
from app.contract_files.models import ContractTextSnapshot, ContractVersion

DOC = (
    "4. Warranties. Intentionally omitted.\n\n"
    "5. Insurance. Intentionally omitted.\n\n"
    "6. Notices. All notices go to 1 Main Street."
)


class _Suggestion:
    def __init__(self, original, replacement):
        self.original_text, self.replacement_text = original, replacement
        self.edit_type, self.rationale, self.risk_level, self.citations = "replace", None, "medium", []


def test_repeated_text_is_not_anchored_to_its_first_block():
    assert anchor_quote(split_blocks(DOC), "Intentionally omitted.") is None


def test_an_ai_edit_to_repeated_text_is_left_unplaced_rather_than_misplaced():
    [rec] = _anchor_suggestions(DOC, [_Suggestion("Intentionally omitted.", "Insurance of $5m is required.")])
    assert rec["matched"] is False and rec["applied"] is False


def test_unique_text_still_anchors_exactly():
    [rec] = _anchor_suggestions(DOC, [_Suggestion("1 Main Street", "2 High Street")])
    assert DOC[rec["start"]:rec["end"]] == "1 Main Street"


class FakeDB:
    def get(self, model, _key):
        if model is ContractVersion:
            return SimpleNamespace(text_snapshot_id="s-1", version_number=3, contract_file_id="f-1")
        if model is ContractTextSnapshot:
            return SimpleNamespace(text=DOC)
        return None


def _propose(monkeypatch, **payload):
    monkeypatch.setattr(routes, "get_contract_for_user",
                        lambda db, **kw: SimpleNamespace(current_authoritative_version_id="v-3", title="MSA"))
    placed = {}

    def stop_after_locating(**kwargs):
        placed.update(kwargs)
        raise RuntimeError("located")

    monkeypatch.setattr(routes, "_build_manual_redline_docx", stop_after_locating)
    body = routes.ManualEditProposal(replacement_text="Deleted.", **payload)
    user = SimpleNamespace(id="u-1", org_id="org-1", full_name="Ann")
    with pytest.raises((HTTPException, RuntimeError)) as exc:
        routes.propose_contract_edit("c-1", body, db=FakeDB(), current_user=user)
    return exc.value, placed


def test_a_stale_offset_on_repeated_text_is_refused(monkeypatch):
    error, _ = _propose(monkeypatch, original_text="Intentionally omitted.", start_hint=3)
    assert isinstance(error, HTTPException) and error.status_code == 409 and "2 times" in error.detail


def test_the_offset_of_the_chosen_occurrence_is_honoured(monkeypatch):
    second = DOC.index("Intentionally omitted.", DOC.index("Intentionally omitted.") + 1)
    _, placed = _propose(monkeypatch, original_text="Intentionally omitted.", start_hint=second)
    assert placed["pre_text"] == DOC[:second]
