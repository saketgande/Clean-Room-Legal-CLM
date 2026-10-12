"""One list of suggestions, decided the same way wherever they came from.

A playbook fix, an assistant edit made in chat and a manual redline are all
ContractEdits anchored to the same version of the contract. These tests guard
the rules that make them one flow:

* they are decided together (one base version = one batch), so deciding one group
  never leaves the other pointing at text that no longer exists;
* deciding the change that fixes a playbook issue decides the issue in the same
  transaction, and only when that is safe;
* the page is told which changes can't be placed in the text.
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.contract_files import redline_edits, routes
from app.contract_files.models import ContractEdit
from app.contract_files.schemas import ContractEditResponse
from app.core.enums import ContractVersionSource

BASE = "1. Term. One year.\n\n2. Payment. Net 30.\n\n3. Law. Delaware."


def _edit(
    eid,
    original,
    replacement,
    *,
    proposal="p-1",
    base="v-1",
    status="proposed",
    edit_type="replace",
    deviation=None,
    deviation_entry_type="playbook_deviation",
    located=True,
):
    start = BASE.find(original) if located and original else -1
    citation = [
        {"type": "assistant_edit_version", "contract_version_id": proposal},
        {
            "type": "anchor",
            "start": start,
            "end": start + len(original) if start >= 0 else -1,
            "matched": start >= 0,
            "applied": start >= 0,
        },
    ]
    if deviation:
        citation.append({"type": deviation_entry_type, "playbook_deviation_id": deviation})
    return ContractEdit(
        id=eid,
        org_id="org-1",
        contract_id="c-1",
        contract_version_id=base,
        edit_type=edit_type,
        status=status,
        original_text=original or None,
        replacement_text=replacement,
        citation=citation,
    )


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class FakeDB:
    """Just enough of a Session for the helpers under test."""

    def __init__(self, edits=(), deviations=()):
        self.edits = list(edits)
        self.deviations = {d.id: d for d in deviations}
        self.commits = 0
        self.statements = []

    def scalars(self, statement):
        # The fake returns every row it holds; the SQL it was asked is checked separately.
        self.statements.append(str(statement.compile(compile_kwargs={"literal_binds": True})))
        return _Rows(self.edits)

    def get(self, _model, key):
        return self.deviations.get(key)

    def commit(self):
        self.commits += 1


def _deviation(did="d-1", *, status="open", contract_id="c-1", org_id="org-1", severity="medium"):
    return SimpleNamespace(id=did, status=status, contract_id=contract_id, org_id=org_id, severity=severity)


class FakePlaybooks:
    def __init__(self):
        self.calls = []

    def record_deviation_decision(self, *, deviation, decision, rationale, user):
        self.calls.append({"deviation": deviation.id, "decision": decision, "rationale": rationale})
        deviation.status = decision


USER = SimpleNamespace(id="u-1", org_id="org-1")
CONTRACT = SimpleNamespace(id="c-1", current_authoritative_version_id="v-1")


@pytest.fixture
def can_run_playbooks(monkeypatch):
    monkeypatch.setattr(routes.org_access, "effective_permission_values", lambda db, user: {"playbook:run"})


# --- reading an edit ----------------------------------------------------------


def test_the_issue_an_edit_fixes_is_found_in_either_kind_of_citation():
    playbook = _edit("e1", "Net 30.", "Net 60.", deviation="d-1", deviation_entry_type="playbook_run")
    drafted = _edit("e2", "Net 30.", "Net 60.", deviation="d-2")
    chat = _edit("e3", "Net 30.", "Net 60.")
    assert redline_edits.paired_deviation_id(playbook.citation) == "d-1"
    assert redline_edits.paired_deviation_id(drafted.citation) == "d-2"
    assert redline_edits.paired_deviation_id(chat.citation) is None
    assert redline_edits.paired_deviation_id(None) is None


def test_an_edit_the_text_cannot_place_is_not_locatable_unless_it_is_a_new_clause():
    placed = _edit("e1", "Net 30.", "Net 60.")
    lost = _edit("e2", "Arbitration in Paris.", "Courts of London.", located=False)
    new_clause = _edit("e3", "", "4. Notices. In writing.", edit_type="playbook_redline", located=False)
    assert redline_edits.is_locatable(placed.edit_type, placed.citation, placed.replacement_text)
    assert not redline_edits.is_locatable(lost.edit_type, lost.citation, lost.replacement_text)
    assert redline_edits.is_locatable(new_clause.edit_type, new_clause.citation, new_clause.replacement_text)


def test_the_response_tells_the_page_which_issue_and_whether_it_can_be_placed():
    edit = _edit("e1", "Net 30.", "Net 60.", deviation="d-1")
    body = ContractEditResponse.model_validate(edit).model_dump()
    assert body["playbook_deviation_id"] == "d-1" and body["locatable"] is True
    lost = ContractEditResponse.model_validate(_edit("e2", "Nope.", "Never.", located=False))
    assert lost.locatable is False and lost.playbook_deviation_id is None


# --- one base version = one batch ---------------------------------------------


def test_playbook_and_chat_edits_on_the_same_base_are_one_batch():
    playbook = _edit("e1", "One year.", "Two years.", proposal="p-playbook", edit_type="playbook_redline")
    chat = _edit("e2", "Net 30.", "Net 60.", proposal="p-chat")
    unlinked = ContractEdit(id="e4", org_id="org-1", contract_id="c-1", contract_version_id="v-1", citation=None)
    db = FakeDB([playbook, chat, unlinked])
    batch = routes._redline_batch(db, edit=chat)
    # Two different proposal versions, one batch; a row with no proposal link is left out.
    assert {e.id for e in batch} == {"e1", "e2"}


def test_a_batch_is_looked_up_by_base_version_within_this_contract_and_org():
    chat = _edit("e2", "Net 30.", "Net 60.", proposal="p-chat")
    db = FakeDB([chat])
    routes._redline_batch(db, edit=chat)
    [sql] = db.statements
    assert "contract_version_id = 'v-1'" in sql
    assert "contract_id = 'c-1'" in sql and "org_id = 'org-1'" in sql
    # ...and not narrowed to one proposal version, which is what made batches go stale on each other
    assert "p-chat" not in sql


def test_the_decided_edit_is_always_part_of_its_own_batch():
    mine = _edit("e1", "Net 30.", "Net 60.")
    assert routes._redline_batch(FakeDB([]), edit=mine) == [mine]


def test_the_contract_is_not_changed_while_a_chat_suggestion_is_still_undecided():
    accepted_fix = _edit("e1", "One year.", "Two years.", proposal="p-playbook", status="accepted", edit_type="playbook_redline")
    open_chat = _edit("e2", "Net 30.", "Net 60.", proposal="p-chat")
    assert routes._apply_decided_redline(
        None, contract=None, batch=[accepted_fix, open_chat], base_version_id="v-1", proposal_version=None, user=None
    ) is None


def test_overlapping_playbook_and_chat_edits_cannot_both_be_accepted():
    # Before, each run was its own batch, so this clash was only found as a stale 409 later.
    accepted_fix = _edit("e1", "2. Payment. Net 30.", "2. Payment. Net 45.", proposal="p-playbook", status="accepted")
    chat = _edit("e2", "Net 30.", "Net 60.", proposal="p-chat")
    assert "overlaps" in routes._accept_blocker(chat, [accepted_fix, chat])


def test_a_version_built_from_a_mixed_batch_says_where_it_came_from():
    playbook = _edit("e1", "One year.", "Two years.", edit_type="playbook_redline")
    manual = _edit("e2", "Net 30.", "Net 60.", edit_type="manual")
    chat = _edit("e3", "Delaware.", "New York.")
    assert routes._batch_source([playbook]) == ContractVersionSource.PLAYBOOK_REDLINE
    assert routes._batch_source([manual]) == ContractVersionSource.USER_REDLINE
    assert routes._batch_source([chat]) == ContractVersionSource.ASSISTANT_EDIT
    assert routes._batch_source([playbook, chat]) == ContractVersionSource.ASSISTANT_EDIT


# --- deciding a change also decides its issue ----------------------------------


def _close(edit, batch, *, db=None, contract=CONTRACT, comment=None):
    service = FakePlaybooks()
    routes._close_paired_issue(
        db or FakeDB(deviations=[_deviation()]),
        service=service,
        contract=contract,
        edit=edit,
        batch=batch,
        comment=comment,
        user=USER,
    )
    return service.calls


def test_accepting_the_fix_records_the_issue_as_fixed(can_run_playbooks):
    """Applying our suggested wording was saved as "accepted", which playbook insights
    counts as a concession, so enforcing a rule recommended loosening it."""
    edit = _edit("e1", "Net 30.", "Net 60.", status="accepted", deviation="d-1")
    assert _close(edit, [edit]) == [
        {"deviation": "d-1", "decision": "fixed", "rationale": "Accepted the proposed change"}
    ]


def test_rejecting_the_fix_keeps_the_original_wording(can_run_playbooks):
    edit = _edit("e1", "Net 30.", "Net 60.", status="rejected", deviation="d-1")
    [call] = _close(edit, [edit], comment="Not worth fighting for")
    assert call["decision"] == "waived"
    assert call["rationale"] == "Kept the original wording. Not worth fighting for"


@pytest.mark.parametrize("severity", ["high", "critical", "HIGH"])
def test_rejecting_the_fix_for_a_high_risk_does_not_wave_the_risk_away(can_run_playbooks, severity):
    """Reject used to waive the issue as well, so one click on Reject cleared a high risk
    (open count 0, "ready for approval") with no reason on record. A high or critical
    issue now stays open: closing it is its own decision, with a reason."""
    deviation = _deviation(severity=severity)
    edit = _edit("e1", "Net 30.", "Net 60.", status="rejected", deviation="d-1")
    assert _close(edit, [edit], db=FakeDB(deviations=[deviation])) == []
    assert deviation.status == "open"


@pytest.mark.parametrize("severity", ["low", "medium", None])
def test_rejecting_the_fix_for_a_lower_risk_still_keeps_the_original_wording(can_run_playbooks, severity):
    deviation = _deviation(severity=severity)
    edit = _edit("e1", "Net 30.", "Net 60.", status="rejected", deviation="d-1")
    assert [c["decision"] for c in _close(edit, [edit], db=FakeDB(deviations=[deviation]))] == ["waived"]


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_accepting_the_fix_for_a_high_risk_still_closes_it(can_run_playbooks, severity):
    deviation = _deviation(severity=severity)
    edit = _edit("e1", "Net 30.", "Net 60.", status="accepted", deviation="d-1")
    assert [c["decision"] for c in _close(edit, [edit], db=FakeDB(deviations=[deviation]))] == ["fixed"]


def test_the_issue_waits_until_every_change_made_for_it_is_decided(can_run_playbooks):
    first = _edit("e1", "Net 30.", "Net 60.", status="accepted", deviation="d-1")
    second = _edit("e2", "One year.", "Two years.", deviation="d-1")
    assert _close(first, [first, second]) == []
    second.status = "rejected"
    assert [c["decision"] for c in _close(second, [first, second])] == ["fixed"]


def test_changes_that_do_not_fix_an_issue_touch_no_issue(can_run_playbooks):
    chat = _edit("e1", "Net 30.", "Net 60.", status="accepted")
    assert _close(chat, [chat]) == []


def test_a_change_made_for_an_older_version_leaves_the_issue_open(can_run_playbooks):
    # The issue may still apply to the current text; a fresh fix can be drafted for it.
    stale = _edit("e1", "Net 30.", "Net 60.", status="rejected", deviation="d-1", base="v-0")
    assert _close(stale, [stale]) == []


def test_without_permission_to_run_playbooks_the_issue_stays_open(monkeypatch):
    monkeypatch.setattr(routes.org_access, "effective_permission_values", lambda db, user: {"contract:redline"})
    edit = _edit("e1", "Net 30.", "Net 60.", status="accepted", deviation="d-1")
    assert _close(edit, [edit]) == []


@pytest.mark.parametrize(
    "deviation",
    [_deviation(status="accepted"), _deviation(contract_id="c-2"), _deviation(org_id="org-2")],
    ids=["already-decided", "other-contract", "other-org"],
)
def test_only_an_open_issue_of_this_contract_is_closed(can_run_playbooks, deviation):
    edit = _edit("e1", "Net 30.", "Net 60.", status="accepted", deviation="d-1")
    assert _close(edit, [edit], db=FakeDB(deviations=[deviation])) == []


def test_an_issue_that_no_longer_exists_is_ignored(can_run_playbooks):
    edit = _edit("e1", "Net 30.", "Net 60.", status="accepted", deviation="gone")
    assert _close(edit, [edit], db=FakeDB()) == []


# --- the routes ----------------------------------------------------------------


def test_accept_and_reject_close_the_issue_before_the_batch_is_applied():
    for route in (routes.accept_contract_edit, routes.reject_contract_edit):
        src = inspect.getsource(route)
        assert "playbooks_service" in inspect.signature(route).parameters
        assert src.index("_close_paired_issue(") < src.index("_apply_decided_redline(")
        # one transaction: nothing commits between the two writes
        assert src.count("db.commit()") == 1


def _ai_redline(db, payload):
    user = SimpleNamespace(id="u-1", org_id="org-1")
    return asyncio.run(routes.ai_redline_contract("c-1", payload, db=db, current_user=user))


@pytest.fixture
def stub_engine(monkeypatch):
    """Replace the model-backed generator: it 'drafts' edits p-new on base v-1."""
    from app.ai.tool_runtime import tool_runtime

    monkeypatch.setattr(routes, "get_contract_for_user", lambda db, **kw: SimpleNamespace(id="c-1"))
    calls = []

    async def fake_edit_contract(db, *, payload, user, session_id):
        calls.append(payload.instructions)
        return {"status": "created", "base_version_id": "v-1", "contract_version_id": "p-new", "edits": 2}

    monkeypatch.setattr(tool_runtime, "_edit_contract", fake_edit_contract)
    return calls


def test_a_fix_drafted_for_an_issue_is_tied_to_that_issue(stub_engine):
    new_a = _edit("e1", "Net 30.", "Net 60.", proposal="p-new")
    new_b = _edit("e2", "One year.", "Two years.", proposal="p-new")
    earlier = _edit("e3", "Delaware.", "New York.", proposal="p-earlier")
    db = FakeDB([new_a, new_b, earlier], [_deviation()])
    result = _ai_redline(db, routes.AiRedlineRequest(instructions="Fix payment", playbook_deviation_id="d-1"))
    assert result["edits"] == 2 and db.commits == 1
    assert redline_edits.paired_deviation_id(new_a.citation) == "d-1"
    assert redline_edits.paired_deviation_id(new_b.citation) == "d-1"
    assert redline_edits.paired_deviation_id(earlier.citation) is None


def test_a_fix_asked_for_in_chat_is_not_tied_to_any_issue(stub_engine):
    new = _edit("e1", "Net 30.", "Net 60.", proposal="p-new")
    _ai_redline(FakeDB([new]), routes.AiRedlineRequest(instructions="Make payment Net 60"))
    assert redline_edits.paired_deviation_id(new.citation) is None


@pytest.mark.parametrize(
    ("deviation", "code"),
    [(_deviation(contract_id="c-2"), 404), (_deviation(org_id="org-2"), 404), (_deviation(status="accepted"), 409), (None, 404)],
    ids=["other-contract", "other-org", "already-closed", "unknown"],
)
def test_a_fix_cannot_be_tied_to_an_issue_that_is_not_open_on_this_contract(stub_engine, deviation, code):
    db = FakeDB([], [deviation] if deviation else [])
    with pytest.raises(HTTPException) as exc:
        _ai_redline(db, routes.AiRedlineRequest(instructions="Fix it", playbook_deviation_id="d-1"))
    assert exc.value.status_code == code
    assert stub_engine == []  # refused before any model call is paid for
