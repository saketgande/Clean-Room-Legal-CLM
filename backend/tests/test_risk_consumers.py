"""Step 3c: every consumer reads the ONE risk band (contracts.risk.contract_risk),
computed now, and treats "unknown" as "a person has to look" -- never as low.

Before this, authority ranked an unknown band with "low", the workflow AI-review
step trusted a cached score from older text, the fast lane kept its own copy of
the rule, a version built from accepted edits sat at unknown forever, and the
plain summary called unreviewed text "standard, safe paper".
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.models  # noqa: F401  (register every mapper)
from app.approvals import service as approvals_service
from app.auth.models import User
from app.authority.models import AuthorityGrant
from app.authority.service import _current_risk_band, _grant_covers, evaluate_authority
from app.contract_files import routes as file_routes
from app.contract_files.models import ContractFile, ContractTextSnapshot, ContractVersion
from app.contracts import risk as risk_module
from app.contracts import routes as contract_routes
from app.core.config import settings
from app.intake.approval_bridge import IntakeApprovalSubject
from app.workflows import service as workflow_service
from tests import test_nda_fast_lane
from tests.test_nda_fast_lane import _nda, add_review, add_version
from tests.test_redline_accepted_subset import BASE, _edit

db = test_nda_fast_lane.db  # the same rolled-back-transaction fixture


def _risk(band: str, *, note: str | None = None, drivers: list | None = None) -> dict:
    return {
        "band": band, "counts": {"critical": 0, "high": 0, "medium": 0, "low": 0},
        "drivers": drivers or [], "note": note, "playbook_run_ids": [],
    }


# --- authority (Delegation of Authority) ----------------------------------

def _grant(max_risk_band: str) -> SimpleNamespace:
    return SimpleNamespace(
        max_value=None, currency=None, allowed_contract_types=[], allowed_jurisdictions=[],
        max_risk_band=max_risk_band,
    )


def test_unknown_risk_exceeds_every_risk_ceiling():
    """_risk_rank(None) was 0, the same as "low", so a 'low only' grant covered a
    contract nobody had reviewed -- and the hand-set risk_level was read as a fallback."""
    contract = SimpleNamespace(
        value_amount=None, currency=None, contract_type="msa", jurisdiction=None,
        risk_band="low", risk_level="low",  # stale / hand-set columns: must not count
    )
    ok, why = _grant_covers(_grant("critical"), contract, risk_band="unknown")
    assert not ok and "isn't confirmed" in why
    assert _grant_covers(_grant("high"), contract, risk_band="high")[0]
    assert not _grant_covers(_grant("medium"), contract, risk_band="high")[0]


def test_an_intake_request_has_unknown_risk_not_its_priority():
    """The requester's priority stood in for a risk band, so filing "low" put a
    request under a low-risk approver's ceiling."""
    request = SimpleNamespace(id="r-1", org_id="o-1", priority="low", field_values={})
    subject = IntakeApprovalSubject(request, type_key=None)
    assert subject.risk_band is None
    assert _current_risk_band(None, subject) == "unknown"


def test_authority_denies_an_unreviewed_contract_and_allows_it_once_reviewed(db):
    """End to end on a real row: the gate computes the band now, so the same grant
    denies before Claude reviewed this text and covers it after."""
    contract = _nda(db)
    version = add_version(db, contract, 1)
    contract.current_authoritative_version_id = version.id
    contract.risk_band = "low"  # a cache nobody refreshed must not open the gate
    user = db.get(User, contract.owner_user_id)
    db.add(AuthorityGrant(
        org_id=contract.org_id, principal_type="user", principal_id=user.id,
        action="contract:approve", max_risk_band="critical",
    ))
    db.flush()

    denied = evaluate_authority(db, user=user, action="contract:approve", contract=contract)
    assert not denied.allowed and "isn't confirmed" in denied.reason

    add_review(db, contract, version)
    allowed = evaluate_authority(db, user=user, action="contract:approve", contract=contract)
    assert allowed.allowed


# --- workflow ai_task: contract_review_agent -------------------------------

def _review_step(monkeypatch, band: str, *, pending: bool = False, threshold: float = 0.6):
    from app.core.database import utcnow

    contract = SimpleNamespace(id="c-1", metadata_json={"auto_review_pending": True} if pending else {})
    monkeypatch.setattr(workflow_service, "_get_contract", lambda _db, _cid: contract)
    monkeypatch.setattr(risk_module, "contract_risk", lambda _db, _c: _risk(band, note="Never reviewed."))
    step = {"type": "ai_task", "name": "AI review",
            "config": {"agent": "contract_review_agent", "escalate_below_confidence": threshold}}
    sr = SimpleNamespace(status="running", note=None, result=None, updated_at=utcnow())
    run = SimpleNamespace(id="run-1", org_id="org-1", request_id="req-1", contract_id="c-1", flow_name="Review")
    fake_db = SimpleNamespace(get=lambda *_: None, commit=lambda: None)
    outcome = asyncio.run(workflow_service._execute_step(
        fake_db, run=run, step=step, sr=sr, actor=SimpleNamespace(id="u-1"),
    ))
    return outcome, sr


def test_unknown_risk_makes_the_ai_review_step_wait_for_a_person(monkeypatch):
    """The step keyed on a cached risk_score; with the band unknown (no review of
    this text) it must give no confidence and ask a person, saying why."""
    outcome, sr = _review_step(monkeypatch, "unknown")
    assert outcome == "wait" and sr.status == "waiting_human"
    assert "Never reviewed." in sr.note
    assert sr.result["confidence"] is None


def test_the_ai_review_step_yields_while_the_playbook_review_is_pending(monkeypatch):
    """Judging the band before the background review lands would read the previous
    text's answer (or unknown) for text the review hasn't seen."""
    outcome, _ = _review_step(monkeypatch, "low", pending=True)
    assert outcome == "yield"


def test_a_low_band_clears_the_ai_review_step(monkeypatch):
    """Guards against over-correcting: a reviewed, low-risk contract must still flow."""
    outcome, sr = _review_step(monkeypatch, "low")
    assert outcome == "advance" and sr.result["confidence"] == 0.95


# --- NDA fast lane ----------------------------------------------------------

@pytest.mark.parametrize(("band", "expected"), [("low", True), ("medium", False), ("unknown", False)])
def test_the_fast_lane_follows_the_computed_band_not_the_cache(monkeypatch, band, expected):
    """The fast lane kept its own run / open-high queries next to the cached band, so
    the two could disagree; now the band alone decides, and a cached 'low' is ignored."""
    monkeypatch.setattr(settings, "nda_fast_lane_enabled", True)
    monkeypatch.setattr(risk_module, "contract_risk", lambda _db, _c: _risk(band))
    contract = SimpleNamespace(contract_type="nda", value_amount=None, currency=None, risk_band="low")
    assert bool(approvals_service._fast_lane_reason(None, contract=contract)) is expected


# --- accept-edits re-review ---------------------------------------------------

def test_a_version_built_from_accepted_edits_queues_a_re_review(monkeypatch):
    """Accepted wording is new text, so the band goes unknown; without the flag the
    contract sat at unknown until someone clicked "Re-run review"."""
    base = ContractVersion(id="v1", contract_file_id="f1", text_snapshot_id="s1")
    rows = {
        ContractVersion: base,
        ContractTextSnapshot: ContractTextSnapshot(id="s1", text=BASE, page_map=None),
        ContractFile: ContractFile(id="f1", org_id="org-1"),
    }
    fake_db = SimpleNamespace(get=lambda model, _id: rows.get(model), add=lambda _r: None, flush=lambda: None)
    for name in ("write_audit_log", "write_timeline_event", "_persist_document_elements", "promote_version"):
        monkeypatch.setattr(file_routes, name, lambda *_a, **_k: None)
    monkeypatch.setattr(file_routes, "_store_generated_docx", lambda *_a, **_k: SimpleNamespace(id="so-1"))
    monkeypatch.setattr(file_routes, "_build_plain_docx", lambda **_k: b"docx")
    monkeypatch.setattr(file_routes, "next_version_number", lambda *_a: 2)
    seen: list[dict] = []
    monkeypatch.setattr(
        file_routes, "requeue_contract_ai_jobs",
        lambda _db, *, contract, **_k: seen.append(dict(contract.metadata_json)),
    )
    contract = SimpleNamespace(
        id="c-1", title="MSA", current_authoritative_version_id="v1", metadata_json={"x": 1},
    )
    edit = _edit("One year.", "Two years.", status="accepted")

    new_version = file_routes._apply_decided_redline(
        fake_db, contract=contract, batch=[edit], base_version_id="v1",
        proposal_version=SimpleNamespace(id="p-1"), user=SimpleNamespace(id="u-1", org_id="org-1"),
    )

    assert new_version is not None
    # Set BEFORE the jobs are queued: _maybe_auto_review reads it after clause extraction.
    assert seen == [{"x": 1, "auto_review_pending": True, "auto_review_reason": "accepted_edits"}]


# --- what people read ---------------------------------------------------------

class _NoDevs:
    def scalars(self, _q):
        return SimpleNamespace(all=list)


def _plain_summary(monkeypatch, risk: dict) -> dict:
    monkeypatch.setattr(contract_routes, "contract_risk", lambda _db, _c: risk)
    service = SimpleNamespace(get_contract_for_user=lambda **_k: SimpleNamespace(id="c-1", title="MSA"))
    return asyncio.run(contract_routes.contract_plain_summary(
        contract_id="c-1", db=_NoDevs(), service=service,
        current_user=SimpleNamespace(id="u-1", org_id="org-1"), claude_client=None,
    ))


def test_the_plain_summary_never_calls_unreviewed_text_safe(monkeypatch):
    """With no open issues the fallback said "this looks like standard, safe paper you
    can proceed with" -- even when nothing had reviewed the text."""
    out = _plain_summary(monkeypatch, _risk("unknown", note="Never reviewed against the NDA playbook."))
    assert out["generated"] is False
    assert "Risk not assessed" in out["summary"] and "Never reviewed" in out["summary"]
    assert "safe paper" not in out["summary"] and "proceed with" not in out["summary"]


def test_the_plain_summary_lists_a_conceded_issue(monkeypatch):
    """A waived high still counts toward the band, so the summary must show it rather
    than say no issues were found."""
    driver = {"deviation_id": "d-1", "issue": "Uncapped liability", "severity": "high",
              "status": "waived", "conceded": True}
    risk = _risk("high", drivers=[driver])
    risk["counts"]["high"] = 1
    out = _plain_summary(monkeypatch, risk)
    assert "Overall high risk" in out["summary"]
    assert "Uncapped liability (conceded" in out["summary"]


def test_get_risk_returns_the_band_computed_now(monkeypatch):
    """GET /risk returned the stored summary, which could describe older text."""
    monkeypatch.setattr(contract_routes, "contract_risk", lambda _db, _c: _risk("medium"))
    service = SimpleNamespace(get_contract_for_user=lambda **_k: SimpleNamespace(risk_summary={"band": "low"}))
    out = contract_routes.get_contract_risk(
        contract_id="c-1", db=None, service=service, current_user=SimpleNamespace(id="u-1"),
    )
    assert out["band"] == "medium"
