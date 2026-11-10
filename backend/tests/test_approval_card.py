"""The approver's summary card (spec 009): what it shows, where each fact came from, and that
the workflow's Approval step alone picks the view.

Each test names the failure it guards: a made-up value, an unconfirmed number that looks
confirmed, a stale clause shown as current, the wrong view for a step, a card that leaks across
orgs or past an ethical wall, or a card that starts AI work.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.approvals.models import ApprovalRequest
from app.auth.models import User
from app.contract_brain.models import ClauseExtraction
from app.contract_files.models import (
    ContractFile,
    ContractTextSnapshot,
    ContractVersion,
    StorageObject,
)
from app.contracts import risk as risk_mod
from app.contracts.access import ContractAccessService
from app.contracts.models import Contract
from app.core.database import engine, new_uuid, utcnow
from app.intake import approval_summary as summary
from app.intake import routes as intake_routes
from app.intake import service as intake_service
from app.intake.models import IntakeRequest
from app.jobs.models import JobRun
from app.organizations.models import Organization
from app.renewals.models import RenewalEvent
from app.workflows import service as wf
from app.workflows.models import Workflow, WorkflowRun, WorkflowStepRun


@pytest.fixture
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _user(db, org_id, label):
    u = User(org_id=org_id, email=f"{label}-{uuid.uuid4().hex[:6]}@example.com", full_name=label,
             hashed_password="h", status="active")
    db.add(u)
    db.flush()
    return u


class World:
    """An org, a requester, a request, optionally its contract (two versions) and a run whose one
    Approval step has `view` set."""

    def __init__(self, db, *, contract=True, view=None, field_values=None, team_name="Finance"):
        self.db = db
        self.org = Organization(id=new_uuid(), name="Card Co", slug=f"card-{uuid.uuid4().hex[:8]}")
        db.add(self.org)
        db.flush()
        self.requester = _user(db, self.org.id, "requester")
        self.approver = _user(db, self.org.id, "approver")
        self.contract = None
        if contract:
            self.contract = Contract(org_id=self.org.id, title="MSA", owner_user_id=self.requester.id,
                                     metadata_json={})
            db.add(self.contract)
            db.flush()
            self.old_version = self._version(1)
            self.current_version = self._version(2)
            self.contract.current_authoritative_version_id = self.current_version.id
            db.flush()
        self.request = IntakeRequest(
            org_id=self.org.id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form",
            requester_user_id=self.requester.id, type_label="New agreement Request", description="t",
            field_values=field_values or {}, priority="Medium", status="open", stage="new",
            submitted_at=utcnow(), created_by_user_id=self.requester.id,
            updated_by_user_id=self.requester.id,
            contract_id=self.contract.id if self.contract else None,
        )
        db.add(self.request)
        db.flush()
        config = {"assign_by": "Specific person", "assignee_user_id": self.approver.id,
                  "team_name": team_name}
        if view is not None:
            config["summary"] = view
        self.run = WorkflowRun(
            org_id=self.org.id, request_id=self.request.id, flow_name="Ladder", flow_version=1,
            steps=[{"id": "s0", "type": "approval", "name": "Approval", "config": config}],
            status="waiting", current_index=0, contract_id=self.contract.id if self.contract else None,
        )
        db.add(self.run)
        db.flush()
        self.approval = ApprovalRequest(
            org_id=self.org.id,
            contract_id=self.contract.id if self.contract else None,
            intake_request_id=None if self.contract else self.request.id,
            contract_version_id=self.current_version.id if self.contract else None,
            status="pending", requested_by_user_id=self.requester.id,
            approver_user_id=self.approver.id, step_order=1, mode="any",
            metadata_json={"submission_batch_id": "b1"},
            created_by_user_id=self.requester.id, updated_by_user_id=self.requester.id,
        )
        db.add(self.approval)
        db.flush()
        db.add(WorkflowStepRun(
            org_id=self.org.id, flow_run_id=self.run.id, idx=0, step_type="approval",
            step_name="Approval", status="waiting_job", result={"approval_ids": [self.approval.id]},
        ))
        db.flush()

    def _version(self, number):
        file = ContractFile(org_id=self.org.id, contract_id=self.contract.id, file_label=f"v{number}")
        self.db.add(file)
        self.db.flush()
        stored = StorageObject(org_id=self.org.id, storage_key=f"t/{uuid.uuid4().hex}", filename="a.docx",
                               mime_type="application/octet-stream", size_bytes=1,
                               sha256_hash=uuid.uuid4().hex)
        self.db.add(stored)
        self.db.flush()
        v = ContractVersion(org_id=self.org.id, contract_id=self.contract.id, contract_file_id=file.id,
                            version_number=number, storage_object_id=stored.id, source="upload",
                            is_authoritative=(number == 2))
        self.db.add(v)
        self.db.flush()
        return v

    def clause(self, clause_type, text, *, version=None, stale=False, start=0):
        version = version or self.current_version
        snap = self.db.scalar(select(ContractTextSnapshot).where(
            ContractTextSnapshot.contract_version_id == version.id))
        if snap is None:
            snap = ContractTextSnapshot(org_id=self.org.id, contract_id=self.contract.id,
                                        contract_version_id=version.id, text="x", extraction_method="test")
            self.db.add(snap)
            self.db.flush()
        row = ClauseExtraction(org_id=self.org.id, contract_id=self.contract.id,
                               contract_version_id=version.id, text_snapshot_id=snap.id,
                               clause_type=clause_type, text=text, start_char=start, is_stale=stale)
        self.db.add(row)
        self.db.flush()
        return row

    def build(self, approval_request_id=None, actor=None):
        return summary.build_summary(self.db, actor=actor or self.requester, request=self.request,
                                     approval_request_id=approval_request_id)

    def facts(self, **kw):
        return {f["key"]: f for f in self.build(**kw)["fields"]}


def test_tags_follow_how_the_column_was_set(db):
    """A form number copied onto the contract must not read as confirmed (FR-5, FR-29)."""
    w = World(db)
    for source, tag in (("user", "verified"), ("change_request", "verified"),
                        ("form", "requester-stated"), ("ai", "extracted"),
                        ("computed", "extracted"), (None, "extracted")):
        w.contract.metadata_json = {"field_sources": {"value_amount": source}} if source else {}
        assert summary.tag_for(w.contract, "value_amount") == tag


def test_value_is_verified_and_a_clause_is_extracted(db):
    """AC-7: a confirmed value says verified while a read-out clause says extracted."""
    w = World(db, view="finance")
    w.contract.value_amount, w.contract.currency = 1200000, "INR"
    w.contract.metadata_json = {"field_sources": {"value_amount": "user"}}
    w.clause("limitation_of_liability", "Liability is capped at fees paid.")
    f = w.facts()
    assert (f["value"]["value"], f["value"]["source"]) == ("INR 1,200,000.00", "verified")
    assert (f["liability"]["source"], f["liability"]["quote"]) == ("extracted", True)


def test_missing_facts_are_null_not_invented(db):
    """AC-8: no payment clause, dates, renewal data -> value and source are both null."""
    w = World(db, view="finance")
    f = w.facts()
    for key in ("value", "term", "payment_terms", "liability", "renewal"):
        assert (f[key]["value"], f[key]["source"]) == (None, None)


def test_term_text_and_its_source(db):
    """The term names both dates, and one unconfirmed date makes the whole term unconfirmed."""
    w = World(db, view="finance")
    w.contract.effective_date, w.contract.expiration_date = date(2026, 11, 1), date(2028, 10, 31)
    w.contract.metadata_json = {"field_sources": {"effective_date": "user", "expiration_date": "form"}}
    t = w.facts()["term"]
    assert (t["value"], t["source"]) == ("2026-11-01 to 2028-10-31", "requester-stated")
    w.contract.expiration_date = None
    assert w.facts()["term"]["value"] == "From 2026-11-01"
    assert w.facts()["term"]["source"] == "verified"


def test_stale_and_older_version_clauses_and_renewals_are_skipped(db):
    """AC-9: a clause on an older version, a stale one, or a renewal on an older version must
    not be shown as if it described the current document."""
    w = World(db, view="finance")
    w.clause("payment_terms", "old version wording", version=w.old_version)
    w.clause("limitation_of_liability", "stale wording", stale=True)
    db.add(RenewalEvent(org_id=w.org.id, contract_id=w.contract.id,
                        contract_version_id=w.old_version.id, metadata_json={"auto_renewal": True}))
    db.flush()
    f = w.facts()
    assert f["payment_terms"]["value"] is None
    assert f["liability"]["value"] is None
    assert f["renewal"]["value"] is None


def test_renewal_from_the_current_version(db):
    """Auto-renewal wording and the notice date come from the current version's renewal event."""
    w = World(db, view="finance")
    db.add(RenewalEvent(org_id=w.org.id, contract_id=w.contract.id,
                        contract_version_id=w.current_version.id, notice_date=date(2028, 8, 2),
                        metadata_json={"auto_renewal": True}))
    db.flush()
    assert w.facts()["renewal"]["value"] == "Renews automatically; notice by 2028-08-02"


def test_snippet_quotes_and_shortens_long_clauses(db):
    """AC-10: clause wording is shown as text (not a number) and a long clause is cut to 1500."""
    assert summary.snippet("a  b\n c") == "a b c"
    long = summary.snippet("word " * 800)
    assert len(long) <= 1500 and long.endswith("…")
    w = World(db, view="finance")
    w.clause("limitation_of_liability", "x" * 1000)
    f = w.facts()["liability"]
    assert f["quote"] is True and f["value"] == summary.snippet("x" * 1000)


def test_issue_text_is_returned_whole_up_to_the_cap():
    """An approver cannot read the whole finding: a 1200-char issue must come back in full and
    only a 2600-char one is cut at the cap, with an ellipsis."""
    mid = "a" * 1200
    assert summary._driver_lines([{"severity": "high", "issue": mid}], 3) == f"high: {mid}"
    cut = summary._driver_lines([{"severity": "high", "issue": "b" * 2600}], 3)
    assert cut == "high: " + "b" * 2000 + "…"


def test_first_clause_by_offset_wins(db):
    """Two clauses of one type: the earlier in the document is the one quoted."""
    w = World(db, view="finance")
    w.clause("payment_terms", "second", start=500)
    w.clause("payment_terms", "first", start=10)
    assert w.facts()["payment_terms"]["value"] == "first"


def test_profile_field_lists(db):
    """AC-11..14: each view shows exactly its list, unset is general, and the team's name
    never picks the view."""
    expected = {
        "finance": ["value", "term", "payment_terms", "liability", "renewal"],
        "legal": ["playbook_risk", "indemnity", "termination", "governing_law", "non_standard"],
        "general": ["value", "term", "overall_risk", "top_issues"],
    }
    for view, keys in expected.items():
        w = World(db, view=view)
        assert [f["key"] for f in w.build()["fields"]] == keys
    unset = World(db)
    assert unset.build()["profile"] == "general"
    named_finance_but_legal = World(db, view="legal", team_name="Finance")
    assert named_finance_but_legal.build()["profile"] == "legal"


def test_governing_law_prefers_the_contract_column(db):
    """Jurisdiction on the record beats a clause, and a clause is quoted only as a fallback."""
    w = World(db, view="legal")
    w.clause("governing_law", "Laws of England.")
    assert w.facts()["governing_law"]["value"] == "Laws of England."
    w.contract.jurisdiction = "Delaware"
    f = w.facts()["governing_law"]
    assert (f["value"], f["quote"]) == ("Delaware", False)


def test_open_risk_equals_contract_risk(db, monkeypatch):
    """AC-21: the card's counts are the page header's own numbers, and risk facts read the same
    result."""
    w = World(db, view="legal")
    fake = {"band": "high", "counts": {"critical": 2, "high": 1, "medium": 0, "low": 0},
            "drivers": [{"severity": "critical", "issue": "Uncapped liability"},
                        {"severity": "high", "issue": "No cure period"}]}
    monkeypatch.setattr(risk_mod, "contract_risk", lambda _db, _c: fake)
    out = w.build()
    assert out["open_risk"] == {"critical": 2, "high": 1}
    assert summary.open_risk_counts(db, w.contract) == out["open_risk"]
    f = {x["key"]: x for x in out["fields"]}
    assert f["playbook_risk"]["value"] == "2 critical · 1 high"
    assert f["non_standard"]["value"] == "critical: Uncapped liability\nhigh: No cure period"
    assert summary.open_risk_counts(db, None) == {"critical": 0, "high": 0}


def test_general_view_risk_lines(db, monkeypatch):
    """Top issues is capped at three and an unknown band is null, never 'Low'."""
    w = World(db, view="general")
    drivers = [{"severity": "high", "issue": f"issue {i}"} for i in range(5)]
    monkeypatch.setattr(risk_mod, "contract_risk", lambda _d, _c: {
        "band": "medium", "counts": {"critical": 0, "high": 5, "medium": 0, "low": 0}, "drivers": drivers})
    f = w.facts()
    assert f["overall_risk"]["value"] == "Medium"
    assert f["top_issues"]["value"].count("\n") == 2
    monkeypatch.setattr(risk_mod, "contract_risk", lambda _d, _c: {
        "band": "unknown", "counts": {"critical": 0, "high": 0, "medium": 0, "low": 0}, "drivers": []})
    f = w.facts()
    assert f["overall_risk"]["value"] is None and f["top_issues"]["value"] is None


def test_no_contract_uses_the_requesters_own_answers(db):
    """AC-16, AC-31: before a contract exists the form's answers show as requester-stated and
    everything else is null (FR-12, FR-29)."""
    fv = {"value": "50000", "currency": "INR", "start_date": "2026-01-01", "end_date": "2026-12-31",
          "payment_terms": "Net 30", "governing_law": "India", "term": "Renews automatically",
          "renewal_term": "for 1 year", "notice_days": "60 days"}
    w = World(db, contract=False, view="finance", field_values=fv)
    f = w.facts()
    assert (f["value"]["value"], f["value"]["source"]) == ("INR 50,000.00", "requester-stated")
    assert f["term"]["value"] == "2026-01-01 to 2026-12-31"
    assert f["payment_terms"]["value"] == "Net 30" and f["payment_terms"]["source"] == "requester-stated"
    assert f["renewal"]["value"] == "Renews automatically, each renewal for 1 year, 60 days notice"
    assert f["liability"]["value"] is None
    legal = World(db, contract=False, view="legal", field_values={"governing_law": "India",
                                                                   "nonstandard": "  custom  "})
    g = legal.facts()
    assert g["governing_law"]["value"] == "India" and g["non_standard"]["value"] == "custom"
    assert g["playbook_risk"]["value"] is None
    assert legal.build()["open_risk"] == {"critical": 0, "high": 0}


def test_no_contract_blank_form_is_all_null(db):
    """A form with no value must not show 0 or a default."""
    w = World(db, contract=False, view="finance", field_values={"value": "  "})
    assert all(f["value"] is None for f in w.build()["fields"])


def test_summary_starts_no_ai_work(db, monkeypatch):
    """AC-6: opening the card must not queue a job or call the model."""
    import app.jobs.service as jobs

    def boom(*_a, **_k):
        raise AssertionError("the card must not start AI work")

    monkeypatch.setattr(jobs, "create_job", boom, raising=False)
    monkeypatch.setattr(jobs, "dispatch_job", boom, raising=False)
    w = World(db, view="finance")
    before = db.scalar(select(func.count()).select_from(JobRun))
    w.build()
    assert db.scalar(select(func.count()).select_from(JobRun)) == before


def test_named_approval_must_belong_to_the_request(db):
    """A rung id from another request must not pick (or reveal) a view: 404."""
    w = World(db)
    other = World(db)
    with pytest.raises(HTTPException) as e:
        w.build(approval_request_id=other.approval.id)
    assert e.value.status_code == 404 and "Approval not found" in e.value.detail
    assert w.build(approval_request_id=w.approval.id)["approval_request_id"] == w.approval.id
    assert w.build()["approval_request_id"] == w.approval.id


def test_walled_contract_is_404(db, monkeypatch):
    """A contract the user is walled from must not leak through the card."""
    w = World(db)
    monkeypatch.setattr(ContractAccessService, "user_can_access_contract", lambda *_a, **_k: False)
    with pytest.raises(HTTPException) as e:
        w.build()
    assert (e.value.status_code, e.value.detail) == (404, "Contract not found")


def test_another_orgs_user_gets_nothing(db):
    """AC-5: a user from another org cannot read the card's facts."""
    w = World(db)
    other_org = Organization(id=new_uuid(), name="Other", slug=f"o-{uuid.uuid4().hex[:8]}")
    db.add(other_org)
    db.flush()
    stranger = _user(db, other_org.id, "stranger")
    with pytest.raises(HTTPException) as e:
        w.build(actor=stranger)
    assert e.value.status_code == 404


def test_subject_contract_follows_the_approval(db):
    """The risk gate must read the contract an approval is really about, org-checked."""
    w = World(db)
    assert summary.subject_contract(db, w.approval).id == w.contract.id
    via_request = ApprovalRequest(org_id=w.org.id, intake_request_id=w.request.id, status="pending",
                                  step_order=1, mode="any")
    assert summary.subject_contract(db, via_request).id == w.contract.id
    bare = World(db, contract=False)
    assert summary.subject_contract(db, bare.approval) is None


def test_unknown_view_is_refused_and_known_ones_kept():
    """FR-13: a typo in the designer must be a 422, not a silent fallback to General."""
    step = {"type": "approval", "name": "A", "config": {"summary": "finance"}}
    assert wf._clean_steps([step])[0]["config"]["summary"] == "finance"
    with pytest.raises(HTTPException) as e:
        wf._clean_steps([{**step, "config": {"summary": "budget"}}])
    assert (e.value.status_code, e.value.detail) == (422, "Approver view must be general, finance or legal")
    assert "summary" not in wf._clean_steps([{**step, "config": {}}])[0]["config"]


def test_run_keeps_its_own_copy_of_the_view(db):
    """AC-15, D9: editing the workflow later changes runs started afterwards, not this one."""
    w = World(db, view="finance")
    flow = Workflow(org_id=w.org.id, name=f"F-{uuid.uuid4().hex[:6]}",
                    steps=wf._clean_steps([{"type": "approval", "name": "A", "config": {"summary": "finance"}}]),
                    criteria={}, enabled=False)
    db.add(flow)
    db.flush()
    flow.steps = wf._clean_steps([{"type": "approval", "name": "A", "config": {"summary": "legal"}}])
    db.flush()
    assert wf.approval_view_for(db, run=w.run, approval_request_id=w.approval.id) == "finance"
    assert wf.approval_view_for(db, run=None, approval_request_id=None) == "general"
    w.run.steps = [{"type": "approval", "config": {"summary": "nonsense"}}]
    assert wf.approval_view_for(db, run=w.run, approval_request_id=w.approval.id) == "general"


# ---- T003: the chain the request page reads (can_decide, decisions) and the summary route ----

def _chain(w, actor=None):
    return intake_service.get_approval_chain(w.db, actor=actor or w.requester, request_id=w.request.id)


def _decide(w, user, decision, comment=None, approval=None):
    return asyncio.run(wf_approvals_decide(w.db, user, approval or w.approval, decision, comment))


async def wf_approvals_decide(db, user, approval, decision, comment):
    from app.approvals.service import decide_in_app

    return await decide_in_app(db, user=user, approval=approval, decision=decision, comment=comment)


def test_requester_sees_the_chain_but_cannot_decide(db):
    """The requester reads the rungs (intake:create gate) yet never gets Approve buttons."""
    w = World(db, contract=False)
    rung = _chain(w)[0]
    assert rung["approval_request_id"] == w.approval.id
    assert rung["can_decide"] is False and rung["decisions"] == []


def test_assigned_approver_can_decide_until_they_have(db):
    """can_decide must be true only for the named approver, and false once they decided."""
    w = World(db, contract=False)
    stranger = _user(db, w.org.id, "bystander")
    assert intake_service.IntakeService(db)._serialize_chain([w.approval], actor=w.approver)[0]["can_decide"]
    assert not intake_service.IntakeService(db)._serialize_chain([w.approval], actor=stranger)[0]["can_decide"]
    _decide(w, w.approver, "approve", "  fine  ")
    rung = intake_service.IntakeService(db)._serialize_chain([w.approval], actor=w.approver)[0]
    assert rung["can_decide"] is False
    assert [(d["approver_name"], d["decision"], d["comment"]) for d in rung["decisions"]] == [
        ("approver", "approve", "fine")]
    assert rung["decisions"][0]["approver_user_id"] == w.approver.id


def test_planned_rungs_cannot_be_decided(db):
    """A planned rung has no approval row: it must say can_decide false and no decisions."""
    w = World(db, contract=False)
    db.delete(w.approval)
    db.flush()
    w.request.status = "open"
    rungs = _chain(w)
    assert rungs and all(r["can_decide"] is False and r["decisions"] == [] for r in rungs)


def test_other_orgs_user_cannot_read_the_chain(db):
    """Org scoping: a stranger from another org gets 404, not the rungs."""
    w = World(db, contract=False)
    other = Organization(id=new_uuid(), name="Other2", slug=f"o-{uuid.uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    with pytest.raises(HTTPException) as e:
        _chain(w, actor=_user(db, other.id, "stranger"))
    assert e.value.status_code == 404


def test_summary_route_404s_on_a_walled_contract(db, monkeypatch):
    """The summary route must not leak a walled contract's facts."""
    w = World(db)
    monkeypatch.setattr(ContractAccessService, "user_can_access_contract", lambda *_a, **_k: False)
    with pytest.raises(HTTPException) as e:
        intake_routes.approval_summary(w.request.id, None, intake_service.IntakeService(db), w.requester)
    assert (e.value.status_code, e.value.detail) == (404, "Contract not found")


def test_summary_route_returns_the_card_for_the_requester(db):
    """The happy path: the requester gets the card for the live rung."""
    w = World(db, contract=False, view="finance")
    out = intake_routes.approval_summary(w.request.id, None, intake_service.IntakeService(db), w.requester)
    assert out["profile"] == "finance" and out["approval_request_id"] == w.approval.id


def test_last_rung_approval_advances_the_request_after_refresh(db):
    """Approving the only rung of a no-contract request must move the workflow on once the
    run is refreshed, not leave it waiting."""
    w = World(db, contract=False)
    _decide(w, w.approver, "approve")
    asyncio.run(wf.refresh_run(db, run=w.run, actor=w.approver))
    db.refresh(w.run)
    assert w.run.status == "complete"


def test_non_final_rung_leaves_the_request_waiting(db):
    """With a second rung still pending, approving the first must not finish the step."""
    w = World(db, contract=False)
    second = ApprovalRequest(
        org_id=w.org.id, intake_request_id=w.request.id, status="waiting",
        requested_by_user_id=w.requester.id, approver_user_id=w.approver.id, step_order=2, mode="any",
        metadata_json={"submission_batch_id": "b1"},
    )
    db.add(second)
    db.flush()
    sr = db.scalar(select(WorkflowStepRun).where(WorkflowStepRun.flow_run_id == w.run.id))
    sr.result = {"approval_ids": [w.approval.id, second.id]}
    db.flush()
    _decide(w, w.approver, "approve")
    asyncio.run(wf.refresh_run(db, run=w.run, actor=w.approver))
    db.refresh(w.run)
    assert w.run.status == "waiting"
    assert w.request.status != "approved"


def test_reject_does_not_approve_the_request(db):
    """A rejection must stop or send back the run, never leave the request approved."""
    w = World(db, contract=False)
    _decide(w, w.approver, "reject", "no")
    asyncio.run(wf.refresh_run(db, run=w.run, actor=w.approver))
    db.refresh(w.run)
    assert w.approval.status == "rejected"
    assert w.run.status != "completed"
    assert w.request.status != "approved"


# ---- D1: only the active rung can be decided ----

def _two_rungs(w):
    """Add a waiting rung 2 for a Legal approver behind the World's pending rung 1."""
    legal = _user(w.db, w.org.id, "legal")
    rung2 = ApprovalRequest(
        org_id=w.org.id, contract_id=None, intake_request_id=w.request.id, status="waiting",
        requested_by_user_id=w.requester.id, approver_user_id=legal.id, step_order=2, mode="any",
        metadata_json={"submission_batch_id": "b1"},
        created_by_user_id=w.requester.id, updated_by_user_id=w.requester.id,
    )
    w.db.add(rung2)
    w.db.flush()
    return legal, rung2


def _can(w, rung, actor):
    return intake_service.IntakeService(w.db)._serialize_chain([rung], actor=actor)[0]["can_decide"]


def test_waiting_rung_is_not_decidable_by_its_own_approver(db):
    """Failure mode: the later rung's approver saw Approve/Reject on a rung that wasn't active
    yet and the click came back as a wrong 409."""
    w = World(db, contract=False)
    legal, rung2 = _two_rungs(w)
    assert _can(w, rung2, legal) is False


def test_rung_becomes_decidable_once_it_is_pending(db):
    """Failure mode: can_decide stuck false after the earlier rung is approved."""
    w = World(db, contract=False)
    legal, rung2 = _two_rungs(w)
    _decide(w, w.approver, "approve")
    db.refresh(rung2)
    assert rung2.status == "pending"
    assert _can(w, rung2, legal) is True


def test_decided_or_cancelled_rung_is_not_decidable(db):
    """Failure mode: an approved/rejected/cancelled rung still offering decide buttons."""
    w = World(db, contract=False)
    for status in ("approved", "rejected", "cancelled"):
        w.approval.status = status
        assert _can(w, w.approval, w.approver) is False
