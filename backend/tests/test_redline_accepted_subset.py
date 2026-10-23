"""APP-03: accepting some edits of a redline must apply exactly those edits —
not the whole proposal version, and not the edits that were rejected."""

import inspect
from types import SimpleNamespace

from app.contract_files import routes
from app.contract_files.models import ContractEdit

BASE = "1. Term. One year.\n\n2. Payment. Net 30.\n\n3. Law. Delaware."


def _edit(original, replacement, *, status="proposed", edit_type="replace", located=True):
    start = BASE.find(original) if located and original else -1
    return ContractEdit(
        edit_type=edit_type,
        status=status,
        original_text=original or None,
        replacement_text=replacement,
        citation=[
            {"type": "assistant_edit_version", "contract_version_id": "proposal-1"},
            {
                "type": "anchor",
                "start": start,
                "end": start + len(original) if start >= 0 else -1,
                "matched": start >= 0,
                "applied": start >= 0,
            },
        ],
    )


def test_only_accepted_edits_are_applied():
    term = _edit("One year.", "Two years.", status="accepted")
    payment = _edit("Net 30.", "Net 60.", status="rejected")
    law = _edit("Delaware.", "New York.", status="accepted")
    text = routes._text_with_accepted_edits(BASE, [term, payment, law])
    assert text == "1. Term. Two years.\n\n2. Payment. Net 30.\n\n3. Law. New York."


def test_accepted_playbook_insertion_is_appended():
    clause = _edit("", "4. Notices. In writing.", status="accepted", edit_type="playbook_redline", located=False)
    assert routes._text_with_accepted_edits(BASE, [clause]) == BASE + "\n\n4. Notices. In writing."


def test_unlocated_edit_cannot_be_accepted():
    lost = _edit("Arbitration in Paris.", "Courts of London.", located=False)
    assert "couldn't be located" in routes._accept_blocker(lost, [lost])


def test_edit_overlapping_an_accepted_edit_cannot_be_accepted():
    clause = _edit("2. Payment. Net 30.", "2. Payment. Net 45.", status="accepted")
    part = _edit("Net 30.", "Net 60.")
    assert "overlaps" in routes._accept_blocker(part, [clause, part])
    assert routes._accept_blocker(_edit("Delaware.", "New York."), [clause]) is None


def test_redline_waits_until_every_edit_is_decided():
    accepted = _edit("One year.", "Two years.", status="accepted")
    undecided = _edit("Net 30.", "Net 60.")
    assert routes._apply_decided_redline(
        None, contract=None, batch=[accepted, undecided], base_version_id="v1", proposal_version=None, user=None
    ) is None


def test_fully_rejected_redline_changes_nothing():
    rejected = _edit("One year.", "Two years.", status="rejected")
    assert routes._apply_decided_redline(
        None, contract=None, batch=[rejected], base_version_id="v1", proposal_version=None, user=None
    ) is None


def test_redline_is_not_applied_when_the_contract_changed_during_review(monkeypatch):
    events = []
    monkeypatch.setattr(routes, "write_timeline_event", lambda db, **kw: events.append(kw["event_type"]))
    accepted = _edit("One year.", "Two years.", status="accepted")
    contract = SimpleNamespace(id="c-1", current_authoritative_version_id="v2")
    user = SimpleNamespace(id="u-1", org_id="org-1")
    result = routes._apply_decided_redline(
        None,
        contract=contract,
        batch=[accepted],
        base_version_id="v1",
        proposal_version=SimpleNamespace(id="proposal-1"),
        user=user,
    )
    assert result is None
    assert events == ["contract.redline_not_applied"]


def test_decisions_never_swap_in_the_whole_proposal():
    for route in (routes.accept_contract_edit, routes.reject_contract_edit):
        src = inspect.getsource(route)
        assert "_apply_decided_redline(" in src
        assert "with_for_update=True" in src
        assert "current_authoritative_version_id = proposal_version.id" not in src
