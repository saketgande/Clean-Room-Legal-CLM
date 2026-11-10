"""Drafting templates: every one fills, edits are checked, versions are kept, the playbook is pinned.

Failures this guards:
* a template using a placeholder drafting doesn't fill — the next request that
  drafts from it 500s with a KeyError instead of producing a contract;
* a saved template with a stray brace or unknown placeholder getting stored —
  it would break drafting for everyone until someone found it;
* an org's saved wording not being what drafting uses, or a reset not going
  back to the shipped default;
* the placeholder list shown on the Templates page drifting from what
  drafting actually fills;
* a later, better-named playbook taking over a template that was already pinned
  to another one, or a GET quietly writing the pin;
* a save made on a stale page overwriting a colleague's newer version.

(That each default passes its playbook literally is test_playbook_library's
``test_our_own_template_passes_the_literal_check``.)

The checks module (``app.drafting_templates.checks``) is a separate unit with its own
tests; here it is replaced by a stand-in that records what the service asks of it.
"""

import shutil
import sys
import types
import uuid
from datetime import UTC

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.main  # the routes bind the real checks module here, before the stand-in below is installed
import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import Permission, Role, User, UserRoleGrant
from app.core.database import engine, new_uuid, utcnow
from app.core.deps import get_current_user, get_db
from app.core.enums import PlaybookStatus
from app.core.models import AuditLog
from app.drafting_templates import schemas as s
from app.drafting_templates import service as svc
from app.drafting_templates.models import (
    DraftingTemplateCheck,
    DraftingTemplateType,
    DraftingTemplateVersion,
)
from app.drafting_templates.service import (
    PLACEHOLDERS,
    TEMPLATES,
    DraftingTemplateService,
    body_sha256,
    check_body,
    current_wording,
    default_body,
    ensure_pinned,
    get_type,
    list_types,
    reviewing_playbook,
)
from app.intake import drafting
from app.org_structure.models import OrgUnit
from app.organizations.models import Organization
from app.playbooks.library import CONTRACT_TYPE_OF_FORM, CONTRACT_TYPE_OF_KIND
from app.playbooks.models import Playbook, PlaybookVersion

_FIELDS = {"value": "4500000", "currency": "INR", "start_date": "2026-10-01", "term": "Fixed end date",
           "end_date": "2027-09-30", "payment_terms": "45 days", "nda_kind": "One-way",
           "nda_direction": "They share", "governing_law": "India"}

_PICK = "Choose one of this organisation's published playbooks."
_NOT_OURS = "That suggestion is not from a check of this template."


@pytest.mark.parametrize("key", sorted(TEMPLATES))
@pytest.mark.parametrize("fields", [{}, _FIELDS], ids=["bare request", "full form"])
def test_every_default_template_fills_completely(key, fields):
    text = drafting.render_document(key, company="Aegis Pharma Ltd", counterparty="Globex Corporation",
                                    effective="2026-10-01", fields=fields)
    assert "Aegis Pharma Ltd" in text and "Globex Corporation" in text
    assert "{" not in text and "}" not in text, f"{key}: an unfilled placeholder"


def test_the_placeholder_list_is_exactly_what_drafting_fills():
    filled = drafting.template_values("nda", company="A", counterparty="B", effective="2026-10-01", fields={})
    assert {p["name"] for p in PLACEHOLDERS} == set(filled)


def test_every_placeholder_has_its_own_plain_name():
    """FR-62: the page shows fields under their plain names, so two fields sharing a label would be
    indistinguishable in the editor, and a missing one would show as raw code."""
    labels = [p["label"] for p in PLACEHOLDERS]
    assert all(labels) and len(set(labels)) == len(labels)
    assert {p["name"]: p["label"] for p in PLACEHOLDERS}["counterparty"] == "The other party"


def test_a_bare_request_drafts_at_the_playbooks_preferred_positions():
    """Blank answers fall back to what the playbook prefers, not its fallback."""
    nda = drafting.template_values("nda", company="A", counterparty="B", effective="2026-10-01", fields={})
    assert nda["term_clause"] == "continues for three (3) years"
    assert nda["survival_clause"] == "continue for five (5) years"
    assert drafting.template_values("consultancy", company="A", counterparty="B", effective="x",
                                    fields={})["payment_days"] == "45"


def test_the_catalog_takes_its_contract_types_from_the_playbook_library():
    """The contract type stored on a draft picks its playbook: typing it a second time here would let
    the catalog and the library drift apart (and the draft be reviewed by no playbook)."""
    assert {k: v["contract_type"] for k, v in TEMPLATES.items()} == {
        "nda": "NDA", "msa": "MSA", "consultancy": "Consultancy Agreement", "sow": "Statement of Work",
        "vendor": "Vendor Agreement", "saas": "Software / SaaS Agreement", "dpa": "DPA",
    }
    for spec in TEMPLATES.values():
        kind, form = spec["agreement_kind"], spec["form_key"]
        assert spec["contract_type"] == (
            CONTRACT_TYPE_OF_KIND[kind] if form == "new_agreement" else CONTRACT_TYPE_OF_FORM[form])


def test_each_shipped_template_has_its_own_binding():
    """FR-49: one request form (and kind) drafts from one template, so two shipped templates on the
    same binding would make 'which template drafts this?' ambiguous."""
    bindings = [(v["form_key"], v["agreement_kind"]) for v in TEMPLATES.values()]
    assert len(set(bindings)) == len(bindings)
    assert svc.binding_of("new_agreement", "NDA") == {
        "form_key": "new_agreement", "form_name": "New agreement", "agreement_kind": "NDA",
        "label": "New agreement · NDA"}
    assert svc.binding_of("sow", None)["label"] == "Statement of Work"


@pytest.mark.parametrize("key", sorted(TEMPLATES))
def test_every_shipped_default_passes_the_save_checks(key):
    """A default that the save rules refuse could never be restored to, and one with no party field
    would draft contracts that name nobody."""
    body = default_body(key)
    check_body(body)
    assert svc.missing_parties(body) == []
    assert {"company", "counterparty"} <= {f["name"] for f in svc.field_uses(body)}


@pytest.mark.parametrize(("body", "says"), [
    ("Dear {client_name},", "Unknown placeholder {client_name}"),
    ("Clause 1 {company", "braces don't pair up"),
    ("   ", "empty"),
])
def test_a_template_drafting_could_not_fill_is_refused(body, says):
    with pytest.raises(HTTPException) as exc:
        check_body(body)
    assert exc.value.status_code == 422
    assert says in exc.value.detail


def test_literal_braces_written_doubled_are_fine():
    check_body("Schedule {{A}} between {company} and {counterparty}.")
    check_body("{{{company}}} and {counterparty}")  # a field inside literal braces
    assert "{Aegis Pharma Ltd}" in svc.render_sample("{{{company}}} and {counterparty}")


@pytest.mark.parametrize(("body", "place", "detail"), [
    ("Line one\nLine two {company}\nDear {client_name},", None,
     ("Unknown placeholder {client_name} (line 3). Use one from the list, or keep it as ordinary text "
      "by writing it as {{client_name}}.")),
    ("a\n\n{company", None, "The braces don't pair up (line 3). Write a literal brace as {{ or }}."),
    ("a\nstray } here", None, "The braces don't pair up (line 2). Write a literal brace as {{ or }}."),
    ("Page {page}", "footer",
     ("Unknown placeholder {page} (in the footer). Use one from the list, or keep it as ordinary text "
      "by writing it as {{page}}.")),
    ("   ", None, "The template is empty."),
])
def test_the_refusal_names_the_line_of_the_problem(body, place, detail):
    """FR-34: a person pasting a long template needs to be told where, not just what."""
    with pytest.raises(HTTPException) as exc:
        check_body(body, place=place)
    assert exc.value.status_code == 422
    assert exc.value.detail == detail


@pytest.mark.parametrize("field", ["{company!r}", "{company:>10}"])
def test_a_conversion_or_format_spec_is_an_unknown_field(field):
    """A field is a bare ``{name}``: str.format would accept these but put quotes or padding into
    the contract (``'Aegis Pharma Ltd'``), and the page flags them, so the server must refuse too."""
    with pytest.raises(HTTPException) as exc:
        check_body(f"Between {field} and {{counterparty}}")
    assert exc.value.status_code == 422
    assert exc.value.detail == (
        f"Unknown placeholder {field} (line 1). Use one from the list, or keep it as ordinary text "
        f"by writing it as {field.replace('{', '{{').replace('}', '}}')}.")
    assert svc.missing_parties(f"{field} {{counterparty}}") == ["company"]  # not read as the field


@pytest.mark.parametrize("field", ["{}", "{0}", "{company.name}", "{company[0]}", "{company!}", "{ company }"])
def test_a_group_that_is_not_a_known_name_is_not_saved(field):
    """Positional, attribute and index groups are not fields drafting fills, and ``{company!}`` is a
    ValueError in str.format: each would break the next draft, so none may be saved."""
    with pytest.raises(HTTPException) as exc:
        check_body(f"Between {field} and {{counterparty}}")
    assert exc.value.status_code == 422


@pytest.mark.parametrize("body", [
    "{company} and {counterparty}", "{{literal}} {company}", "{{{company}}}", "no fields at all",
    "{company}{counterparty}{{}}",
])
def test_whatever_the_save_rules_accept_str_format_fills(body):
    """The save rules are a stricter scanner than str.format; if they ever accepted something
    str.format rejects, the next draft from that template would 500."""
    check_body(body)
    body.format(**svc.SAMPLE_VALUES)


@pytest.fixture
def db():
    """A session whose work is rolled back at the end, so nothing the test saves outlives it."""
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _summary_of(check) -> dict:
    """A TemplateCheckSummary for a check row (the real serializer belongs to checks.py)."""
    findings = check.findings
    return {
        "id": check.id, "state": check.status, "trigger": check.trigger, "unsaved": bool(check.unsaved),
        "template_version": check.template_version, "playbook_name": check.playbook_name,
        "playbook_version_number": check.playbook_version_number,
        "finding_count": None if findings is None else len(findings), "highest_severity": None,
        "text_was_truncated": check.text_was_truncated, "error_code": check.error_code,
        "error_message": check.error_message, "requested_by": None, "on_behalf": False,
        "created_at": check.created_at, "started_at": check.started_at, "finished_at": check.finished_at,
    }


_NEVER_CHECKED = {
    "state": "never_checked", "label": "Never checked", "reason": "Not checked yet.",
    "finding_count": None, "highest_severity": None, "changed": None, "error_code": None,
    "results_vary": False, "decided_by": None, "last_completed": None, "active_check": None,
}


@pytest.fixture(autouse=True)
def checks(monkeypatch):
    """The checks module, replaced by a stand-in that records the requests made of it. Set
    ``checks.next_check`` to the row ``request_check`` should answer with."""
    fake = types.ModuleType("app.drafting_templates.checks")
    fake.requests = []
    fake.next_check = None
    fake.error = None

    def request_check(db, *, org_id, key, trigger, actor_user_id, unsaved_body=None, trigger_detail=None):
        fake.requests.append({"org_id": org_id, "key": key, "trigger": trigger, "actor_user_id": actor_user_id})
        if fake.error is not None:
            raise fake.error
        return fake.next_check

    fake.request_check = request_check
    fake.template_status = lambda db, *, org_id, ttype: dict(_NEVER_CHECKED)
    fake.serialize_check = lambda db, check, *, full: _summary_of(check)
    monkeypatch.setitem(sys.modules, "app.drafting_templates.checks", fake)
    return fake


def _make_user(db: Session, org_id: str, name: str = "admin") -> User:
    user = User(org_id=org_id, email=f"user-{uuid.uuid4().hex[:8]}@example.com", full_name=name,
                hashed_password="hash", status="active")
    db.add(user)
    db.flush()
    return user


def _make_actor(db: Session) -> User:
    """A real organization with one user. (Drafting-template rows point at their organization, so a
    stand-in "first user in the database" does not work on an empty database like CI's.)"""
    org = Organization(id=new_uuid(), name="Drafting Co", slug=f"drafting-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return _make_user(db, org.id)


def _playbook(db: Session, user: User, name: str, *, published: bool = True, deleted: bool = False) -> Playbook:
    state = PlaybookStatus.PUBLISHED if published else PlaybookStatus.DRAFT
    pb = Playbook(org_id=user.org_id, name=name, status=state, created_by_user_id=user.id,
                  updated_by_user_id=user.id, deleted_at=utcnow() if deleted else None)
    db.add(pb)
    db.flush()
    db.add(PlaybookVersion(org_id=user.org_id, playbook_id=pb.id, version_number=1, status=state,
                           created_by_user_id=user.id, updated_by_user_id=user.id))
    db.flush()
    return pb


def _pin_row(db: Session, user: User, key: str, playbook: Playbook) -> None:
    spec = TEMPLATES[key]
    db.add(DraftingTemplateType(
        org_id=user.org_id, key=key, name=spec["name"], label=spec["label"], form_key=spec["form_key"],
        agreement_kind=spec["agreement_kind"], shipped=True, status="active", playbook_id=playbook.id))
    db.flush()


def _check_row(db: Session, user: User, key: str, *, version: int = 0, findings: list | None = None,
               unsaved: bool = False, status: str = "completed", org_id: str | None = None):
    row = DraftingTemplateCheck(
        org_id=org_id or user.org_id, template_key=key, template_version=version,
        wording_sha256=body_sha256(default_body(key)), unsaved=unsaved, trigger="manual", status=status,
        findings=findings, created_by_user_id=user.id)
    db.add(row)
    db.flush()
    return row


_FINDINGS = [
    {"index": 0, "rule_index": 1, "clause_type": "payment_terms", "severity": "high", "issue": "x",
     "quote": None, "quote_template": None, "suggested_fix": "Pay in 30 days.",
     "suggested_fix_template": None, "suggestion_is_wording": True, "approval_required": False},
    {"index": 1, "rule_index": 2, "clause_type": "termination", "severity": "low", "issue": "y",
     "quote": None, "quote_template": None, "suggested_fix": None, "suggested_fix_template": None,
     "suggestion_is_wording": False, "approval_required": False},
]


def _audit(db: Session, org_id: str, action: str) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == action)
                           .order_by(AuditLog.created_at, AuditLog.id)))


def _count(db: Session, model, org_id: str) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.org_id == org_id))


def test_saved_wording_is_what_drafting_uses_and_reset_goes_back(db):
    actor = _make_actor(db)
    service = DraftingTemplateService(db)
    edited = default_body("vendor") + "\n\nSCHEDULE — test-drafting-templates marker for {counterparty}."

    saved = service.save(actor=actor, key="vendor", body=edited, note="test")
    assert saved["changed"] is True
    assert saved["template"]["source"] == "text" and saved["template"]["version"] == 1
    assert service.body_for(org_id=actor.org_id, key="vendor") == edited

    # Saving the same text again adds no version.
    again = service.save(actor=actor, key="vendor", body=edited, note=None)
    assert again["changed"] is False and again["template"]["version"] == 1

    back = service.reset(actor=actor, key="vendor", note=None)
    assert back["changed"] is True
    assert back["template"]["source"] == "default" and back["template"]["version"] == 2
    assert service.body_for(org_id=actor.org_id, key="vendor") == default_body("vendor")
    # The edited wording stays readable in History.
    assert any(v["body"] == edited for v in service.versions(org_id=actor.org_id, key="vendor"))


def test_saving_the_shipped_text_verbatim_keeps_following_the_default(db):
    """A copy of today's default stored as an edit would freeze it: later improvements to the shipped
    text would silently stop reaching this org."""
    actor = _make_actor(db)
    service = DraftingTemplateService(db)
    service.save(actor=actor, key="msa", body=default_body("msa") + "\n\nExtra.", note=None)
    back = service.save(actor=actor, key="msa", body=default_body("msa"), note=None)
    assert back["changed"] is True
    newest = service.versions(org_id=actor.org_id, key="msa")[0]
    assert newest["is_default"] is True and newest["source"] == "default"
    row = db.scalar(select(DraftingTemplateVersion).where(
        DraftingTemplateVersion.org_id == actor.org_id, DraftingTemplateVersion.version == 2))
    assert row.body is None


def test_an_unknown_template_is_not_found(db):
    actor = _make_actor(db)
    service = DraftingTemplateService(db)
    with pytest.raises(HTTPException) as exc:
        service.get(org_id="x", key="customer")
    assert exc.value.status_code == 404
    calls = [
        lambda: service.versions(org_id=actor.org_id, key="customer"),
        lambda: service.save(actor=actor, key="customer", body="{company} {counterparty}", note=None),
        lambda: service.reset(actor=actor, key="customer", note=None),
        lambda: service.restore(actor=actor, key="customer", version=1),
        lambda: service.set_playbook(actor=actor, key="customer", playbook_id="x"),
    ]
    for call in calls:
        with pytest.raises(HTTPException) as err:
            call()
        assert err.value.status_code == 404 and err.value.detail == "No such template"


# --- The pin (D-11) ---------------------------------------------------------------------------


def test_the_first_use_pins_todays_name_match_and_audits_it(db):
    """FR-51: the playbook is locked at first use, and the lock is on the record as the system's act on
    the person's behalf, not the person's own."""
    actor = _make_actor(db)
    pb = _playbook(db, actor, "Services Agreement Playbook")
    ttype = get_type(db, actor.org_id, "msa")
    assert ttype.playbook_id is None

    got = ensure_pinned(db, org_id=actor.org_id, ttype=ttype, actor_user_id=actor.id, commit=True)

    assert got.id == pb.id
    pinned = get_type(db, actor.org_id, "msa")
    assert pinned.playbook_id == pb.id and pinned.row_id is not None
    [row] = _audit(db, actor.org_id, "drafting_template.playbook_pinned")
    assert row.actor_user_id is None
    assert row.metadata_json == {"actor": "system", "on_behalf_of_user_id": actor.id}
    assert row.after == {"key": "msa", "playbook_id": pb.id, "playbook_name": pb.name,
                         "how": "matched by name at first use"}
    # Used again, it is already pinned: no second row, no second audit.
    assert ensure_pinned(db, org_id=actor.org_id, ttype=pinned, actor_user_id=actor.id, commit=True).id == pb.id
    assert len(_audit(db, actor.org_id, "drafting_template.playbook_pinned")) == 1


def test_a_type_nothing_matches_is_not_pinned(db):
    """With no playbook for it there is nothing to lock: inventing a row would later hide a playbook
    published for it."""
    actor = _make_actor(db)
    _playbook(db, actor, "Customer Agreement Playbook")
    ttype = get_type(db, actor.org_id, "msa")
    assert ensure_pinned(db, org_id=actor.org_id, ttype=ttype, actor_user_id=actor.id, commit=True) is None
    assert _count(db, DraftingTemplateType, actor.org_id) == 0
    assert _audit(db, actor.org_id, "drafting_template.playbook_pinned") == []


def test_reading_never_pins_or_writes(db):
    """A GET that pinned would lock a playbook just because someone opened the page, and put a write
    (and the audit lock) in every read path."""
    actor = _make_actor(db)
    _playbook(db, actor, "Master Services Agreement Playbook")
    service = DraftingTemplateService(db)
    before = (_count(db, DraftingTemplateType, actor.org_id), _count(db, AuditLog, actor.org_id),
              _count(db, DraftingTemplateVersion, actor.org_id))

    listed = service.list(org_id=actor.org_id)
    service.get(org_id=actor.org_id, key="msa")
    service.versions(org_id=actor.org_id, key="msa")
    service.options(org_id=actor.org_id)
    ttype = get_type(db, actor.org_id, "msa")
    rev = reviewing_playbook(db, actor.org_id, ttype)

    assert (_count(db, DraftingTemplateType, actor.org_id), _count(db, AuditLog, actor.org_id),
            _count(db, DraftingTemplateVersion, actor.org_id)) == before
    assert rev.pinned is False and rev.available is True and rev.playbook.name.startswith("Master")
    msa = next(t for t in listed if t["key"] == "msa")
    assert msa["playbook"]["pinned"] is False and msa["playbook"]["available"] is True
    assert next(t for t in listed if t["key"] == "nda")["playbook"] is None  # nothing matches it


def test_two_first_uses_end_with_one_pin(db):
    """Two requests that both saw the type unpinned must not each pin: the first stays, and the
    second is told which one won (the upsert only writes where no pin exists)."""
    actor = _make_actor(db)
    first = _playbook(db, actor, "Services Agreement Playbook")
    stale = get_type(db, actor.org_id, "msa")  # both requests read this: not pinned yet
    assert ensure_pinned(db, org_id=actor.org_id, ttype=stale, actor_user_id=actor.id, commit=True).id == first.id

    better = _playbook(db, actor, "Master Services Agreement Playbook")
    assert svc._name_match(db, actor.org_id, "MSA").id == better.id  # the name match has moved on

    got = ensure_pinned(db, org_id=actor.org_id, ttype=stale, actor_user_id=actor.id, commit=True)

    assert got.id == first.id
    assert get_type(db, actor.org_id, "msa").playbook_id == first.id
    assert _count(db, DraftingTemplateType, actor.org_id) == 1
    assert len(_audit(db, actor.org_id, "drafting_template.playbook_pinned")) == 1


def test_a_pinned_template_is_not_taken_over_by_a_better_named_playbook(db):
    """D-11: once pinned, reviewing follows the pin, never today's name match."""
    actor = _make_actor(db)
    first = _playbook(db, actor, "Services Agreement Playbook")
    _pin_row(db, actor, "msa", first)
    _playbook(db, actor, "Master Services Agreement Playbook")

    rev = reviewing_playbook(db, actor.org_id, get_type(db, actor.org_id, "msa"))

    assert rev.pinned is True and rev.playbook.id == first.id and rev.available is True
    assert rev.version.playbook_id == first.id


def test_a_pinned_playbook_that_is_gone_is_unavailable_never_replaced(db):
    """The spec's edge case: a deleted, unpublished or foreign pinned playbook leaves the template with
    No playbook, not quietly reviewed by another playbook with a similar name."""
    actor = _make_actor(db)
    stranger = _make_actor(db)
    _playbook(db, actor, "Master Services Agreement Playbook")  # a tempting replacement
    for pin, expect_playbook in [
        (_playbook(db, actor, "Services Agreement Playbook", deleted=True), True),
        (_playbook(db, actor, "Services Agreement Draft Playbook", published=False), True),
        (_playbook(db, stranger, "Services Agreement Playbook"), False),  # another org's: never ours
    ]:
        ttype = svc.TemplateType("msa", "x", "x", "new_agreement", "Services (MSA)", True, True, None, pin.id)
        rev = reviewing_playbook(db, actor.org_id, ttype)
        assert rev.pinned is True and rev.available is False and rev.version is None
        assert (rev.playbook is not None) is expect_playbook


def test_set_playbook_refuses_a_playbook_that_cannot_review(db):
    """FR-50/51/69: only this organisation's published, undeleted playbooks can be pinned, because
    only those can be guaranteed to review the template's drafts."""
    actor = _make_actor(db)
    stranger = _make_actor(db)
    ids = {
        "another org's": _playbook(db, stranger, "Master Services Agreement Playbook").id,
        "deleted": _playbook(db, actor, "Deleted MSA Playbook", deleted=True).id,
        "unpublished": _playbook(db, actor, "Draft MSA Playbook", published=False).id,
        "unknown": new_uuid(),
    }
    service = DraftingTemplateService(db)
    for what, playbook_id in ids.items():
        with pytest.raises(HTTPException) as exc:
            service.set_playbook(actor=actor, key="msa", playbook_id=playbook_id)
        assert (exc.value.status_code, exc.value.detail) == (422, _PICK), what
    assert _count(db, DraftingTemplateType, actor.org_id) == 0
    assert _audit(db, actor.org_id, "drafting_template.playbook_changed") == []


def test_set_playbook_pins_audits_before_and_after_and_starts_a_check(db, checks):
    actor = _make_actor(db)
    old = _playbook(db, actor, "Services Agreement Playbook")
    new = _playbook(db, actor, "Acme House Playbook")  # shares no word with an MSA
    service = DraftingTemplateService(db)
    checks.next_check = _check_row(db, actor, "msa")

    result = service.set_playbook(actor=actor, key="msa", playbook_id=new.id, note="  legal asked  ")

    s.TemplateMutationResultOut.model_validate(result)
    assert result["changed"] is True and result["check"]["id"] == checks.next_check.id
    assert result["template"]["playbook"]["id"] == new.id and result["template"]["playbook"]["pinned"] is True
    assert get_type(db, actor.org_id, "msa").playbook_id == new.id
    assert checks.requests == [{"org_id": actor.org_id, "key": "msa", "trigger": "playbook_changed",
                                "actor_user_id": actor.id}]
    [row] = _audit(db, actor.org_id, "drafting_template.playbook_changed")
    assert row.actor_user_id == actor.id
    # Before: what reviewed it until now (the name match, for a type not pinned yet).
    assert row.before == {"playbook_id": old.id, "playbook_name": old.name}
    assert row.after == {"playbook_id": new.id, "playbook_name": new.name, "note": "legal asked"}


def test_choosing_the_playbook_it_already_has_changes_nothing(db, checks):
    """FR-37 spirit: re-picking the current pin must not start a paid check or write a second audit."""
    actor = _make_actor(db)
    pb = _playbook(db, actor, "Services Agreement Playbook")
    service = DraftingTemplateService(db)
    assert service.set_playbook(actor=actor, key="msa", playbook_id=pb.id)["changed"] is True
    checks.requests.clear()

    again = service.set_playbook(actor=actor, key="msa", playbook_id=pb.id)

    assert again["changed"] is False and again["check"] is None
    assert checks.requests == []
    assert len(_audit(db, actor.org_id, "drafting_template.playbook_changed")) == 1


# --- Saving, resetting, restoring --------------------------------------------------------------


def test_a_stale_save_is_refused_with_who_and_when_and_writes_nothing(db):
    """FR-59: two people on the same template — the second must not silently overwrite the first."""
    actor = _make_actor(db)
    priya = _make_user(db, actor.org_id, "Priya Shah")
    service = DraftingTemplateService(db)
    service.save(actor=priya, key="vendor", body=default_body("vendor") + "\n\nPriya's clause.",
                 note=None, expected_version=0)
    when = service._latest(actor.org_id, "vendor").created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M")

    with pytest.raises(HTTPException) as exc:
        service.save(actor=actor, key="vendor", body=default_body("vendor") + "\n\nMine.",
                     note=None, expected_version=0)

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        f"Priya Shah saved version 1 of this template at {when} UTC after you opened it. "
        "Your text is still in the editor: compare it with theirs, then save again.")
    assert service._latest(actor.org_id, "vendor").version == 1
    # Having opened the new version, the save goes through.
    saved = service.save(actor=actor, key="vendor", body=default_body("vendor") + "\n\nMine.",
                         note=None, expected_version=1)
    assert saved["template"]["version"] == 2


def test_two_saves_made_on_the_same_version_cannot_both_win(db, monkeypatch):
    """The version check and the insert are not one step, so the unique version number is the real
    guard: the loser gets the same 409, and what the winner saved is untouched."""
    actor = _make_actor(db)
    service = DraftingTemplateService(db)
    service.save(actor=actor, key="msa", body=default_body("msa") + "\n\nWinner.", note=None)
    monkeypatch.setattr(service, "_next_version", lambda org_id, key: 1)  # the loser computed 1 too

    with pytest.raises(HTTPException) as exc:
        service.save(actor=actor, key="msa", body=default_body("msa") + "\n\nLoser.", note=None)

    assert exc.value.status_code == 409 and "saved version 1 of this template" in exc.value.detail
    assert "Winner." in service.body_for(org_id=actor.org_id, key="msa")


@pytest.mark.parametrize(("body", "who"), [
    ("Agreement by {company}.", "the other party"),
    ("Agreement with {counterparty}.", "our entity"),
    ("Agreement between two parties.", "our entity or the other party"),
])
def test_wording_that_names_no_party_needs_an_explicit_yes(db, body, who):
    """FR-35: drafts from this wording would not name a party; that is allowed only on purpose."""
    actor = _make_actor(db)
    service = DraftingTemplateService(db)

    with pytest.raises(HTTPException) as exc:
        service.save(actor=actor, key="msa", body=body, note=None)

    assert exc.value.status_code == 422
    assert exc.value.detail == (f"This wording has no field for {who}, so drafts will not name them. "
                                "Confirm that this is intended to save it.")
    assert _count(db, DraftingTemplateVersion, actor.org_id) == 0
    ok = service.save(actor=actor, key="msa", body=body, note=None, acknowledge_missing_parties=True)
    assert ok["changed"] is True and ok["template"]["version"] == 1


def test_a_wording_that_cannot_fill_is_refused_before_anything_is_written(db, checks):
    actor = _make_actor(db)
    service = DraftingTemplateService(db)
    with pytest.raises(HTTPException) as exc:
        service.save(actor=actor, key="msa", body="{company} {counterparty}\nDear {client_name}", note=None)
    assert exc.value.status_code == 422 and "(line 2)" in exc.value.detail
    assert _count(db, DraftingTemplateVersion, actor.org_id) == 0 and checks.requests == []


def test_applied_findings_are_stored_audited_and_shown_in_history(db):
    """FR-25: the suggestions a version was saved to fix are on the version and in the audit trail,
    with their clause and severity copied from the check (the check row may change later)."""
    actor = _make_actor(db)
    check = _check_row(db, actor, "msa", findings=_FINDINGS)
    service = DraftingTemplateService(db)

    result = service.save(
        actor=actor, key="msa", body=default_body("msa") + "\n\nPayment within 30 days.", note=None,
        applied_findings=[s.AppliedFindingIn(check_id=check.id, finding_index=0)])

    stored = [{"check_id": check.id, "finding_index": 0, "clause_type": "payment_terms", "severity": "high"}]
    assert result["changed"] is True
    row = service._latest(actor.org_id, "msa")
    assert row.applied_findings == stored
    [audit] = _audit(db, actor.org_id, "drafting_template.saved")
    assert audit.after["applied_findings"] == stored
    assert audit.after["wording_sha256"] == body_sha256(row.body) and audit.after["version"] == 1
    newest = service.versions(org_id=actor.org_id, key="msa")[0]
    assert newest["applied_findings"] == stored
    s.DraftingTemplateVersionOut.model_validate(newest)


def test_a_finding_that_is_not_from_a_check_of_this_template_is_refused(db):
    """A made-up, foreign-org or other-template check id must not end up in a version's record."""
    actor = _make_actor(db)
    stranger = _make_actor(db)
    ours = _check_row(db, actor, "msa", findings=_FINDINGS)
    pairs = {
        "another org's check": {"check_id": _check_row(db, stranger, "msa", findings=_FINDINGS).id,
                                "finding_index": 0},
        "another template's check": {"check_id": _check_row(db, actor, "nda", findings=_FINDINGS).id,
                                     "finding_index": 0},
        "no such check": {"check_id": new_uuid(), "finding_index": 0},
        "no such finding": {"check_id": ours.id, "finding_index": 7},
        "a check that found nothing": {"check_id": _check_row(db, actor, "msa", findings=None).id,
                                       "finding_index": 0},
    }
    service = DraftingTemplateService(db)
    for what, pair in pairs.items():
        with pytest.raises(HTTPException) as exc:
            service.save(actor=actor, key="msa", body=default_body("msa") + "\n\nX.", note=None,
                         applied_findings=[pair])
        assert (exc.value.status_code, exc.value.detail) == (422, _NOT_OURS), what
    assert _count(db, DraftingTemplateVersion, actor.org_id) == 0


def test_saving_unchanged_wording_makes_no_version_and_starts_no_check(db, checks):
    """FR-37: no paid review for text that is already the template."""
    actor = _make_actor(db)
    service = DraftingTemplateService(db)

    same = service.save(actor=actor, key="nda", body=default_body("nda"), note="x", expected_version=0)

    assert same["changed"] is False and same["check"] is None and same["template"]["version"] == 0
    assert _count(db, DraftingTemplateVersion, actor.org_id) == 0
    assert checks.requests == [] and _audit(db, actor.org_id, "drafting_template.saved") == []


def test_restore_makes_a_new_version_and_leaves_the_earlier_ones_alone(db, checks):
    """FR-58: loading back never rewrites history — it adds a latest version that says where it came from."""
    actor = _make_actor(db)
    service = DraftingTemplateService(db)
    one = default_body("vendor") + "\n\nFirst edit."
    two = default_body("vendor") + "\n\nSecond edit."
    service.save(actor=actor, key="vendor", body=one, note="one")
    service.save(actor=actor, key="vendor", body=two, note="two")
    checks.requests.clear()

    result = service.restore(actor=actor, key="vendor", version=1, expected_version=2)

    assert result["changed"] is True and result["template"]["version"] == 3
    assert service.body_for(org_id=actor.org_id, key="vendor") == one
    versions = service.versions(org_id=actor.org_id, key="vendor")
    assert [v["version"] for v in versions] == [3, 2, 1, 0]
    assert [v["body"] for v in versions[1:3]] == [two, one]
    assert [v["note"] for v in versions[1:3]] == ["two", "one"]
    assert versions[0]["restored_from_version"] == 1 and versions[0]["note"] == "Restored version 1"
    assert versions[1]["restored_from_version"] is None
    [audit] = _audit(db, actor.org_id, "drafting_template.restored")
    assert audit.after["version"] == 3 and audit.after["restored_from_version"] == 1
    assert [r["trigger"] for r in checks.requests] == ["restored"]


def test_restoring_version_zero_is_a_reset_and_a_missing_version_is_not_found(db, checks):
    actor = _make_actor(db)
    service = DraftingTemplateService(db)
    service.save(actor=actor, key="saas", body=default_body("saas") + "\n\nEdit.", note=None)
    checks.requests.clear()

    with pytest.raises(HTTPException) as exc:
        service.restore(actor=actor, key="saas", version=9)
    assert (exc.value.status_code, exc.value.detail) == (404, "No such version of this template.")
    with pytest.raises(HTTPException) as stale:
        service.restore(actor=actor, key="saas", version=1, expected_version=0)
    assert stale.value.status_code == 409

    back = service.restore(actor=actor, key="saas", version=0, expected_version=1)

    assert back["changed"] is True and back["template"]["source"] == "default"
    assert [r["trigger"] for r in checks.requests] == ["reset"]
    assert len(_audit(db, actor.org_id, "drafting_template.reset")) == 1


def test_reset_on_the_shipped_wording_makes_no_version_and_no_check(db, checks):
    """Spec edge case: nothing to go back to means no version and no paid review."""
    actor = _make_actor(db)
    service = DraftingTemplateService(db)

    nothing = service.reset(actor=actor, key="dpa", note=None, expected_version=0)

    assert nothing["changed"] is False and nothing["check"] is None
    assert [v["version"] for v in service.versions(org_id=actor.org_id, key="dpa")] == [0]
    assert checks.requests == []

    service.save(actor=actor, key="dpa", body=default_body("dpa") + "\n\nEdit.", note=None)
    checks.requests.clear()
    assert service.reset(actor=actor, key="dpa", note="back", expected_version=1)["changed"] is True
    assert [r["trigger"] for r in checks.requests] == ["reset"]
    assert service.reset(actor=actor, key="dpa", note=None)["changed"] is False  # already default again


@pytest.mark.parametrize("trigger", ["saved", "reset", "restored", "playbook_changed"])
def test_the_check_is_requested_only_after_the_change_has_committed(db, checks, monkeypatch, trigger):
    """Lock order: request_check takes the template lock and then the audit lock, so the change and its
    audit row must already be committed — holding the audit lock while waiting on the template lock
    is the freeze of 2026-09-28."""
    actor = _make_actor(db)
    pb = _playbook(db, actor, "Services Agreement Playbook")
    service = DraftingTemplateService(db)
    service.save(actor=actor, key="msa", body=default_body("msa") + "\n\nEdit.", note=None)
    events: list[str] = []
    real_commit = db.commit
    monkeypatch.setattr(db, "commit", lambda: (events.append("commit"), real_commit())[1])
    checks.requests.clear()
    original = checks.request_check
    checks.request_check = lambda *a, **kw: (events.append("request_check"), original(*a, **kw))[1]

    {
        "saved": lambda: service.save(actor=actor, key="msa", body=default_body("msa") + "\n\nMore.", note=None),
        "reset": lambda: service.reset(actor=actor, key="msa", note=None),
        "restored": lambda: service.restore(actor=actor, key="msa", version=1),
        "playbook_changed": lambda: service.set_playbook(actor=actor, key="msa", playbook_id=pb.id),
    }[trigger]()

    assert events == ["commit", "request_check"]
    assert [(r["key"], r["trigger"], r["actor_user_id"]) for r in checks.requests] == [("msa", trigger, actor.id)]


# The service rolls the session back after a failed request, which here also ends the fixture's
# outer transaction; the fixture's own rollback then has nothing left to undo.
@pytest.mark.filterwarnings("ignore:transaction already deassociated")
def test_a_failing_check_request_does_not_turn_a_saved_wording_into_an_error(db, checks):
    """The version and its audit row are committed before the check is asked for; an error from the
    check side must not tell the person their save failed (they would retry and get 'unchanged', and
    no check would ever start)."""
    actor = _make_actor(db)
    service = DraftingTemplateService(db)
    checks.error = RuntimeError("queue is down")

    result = service.save(actor=actor, key="msa", body=default_body("msa") + "\n\nEdit.", note=None)

    assert result["changed"] is True and result["check"] is None
    assert [r["trigger"] for r in checks.requests] == ["saved"]


def test_mutation_results_carry_the_check_summary(db, checks):
    actor = _make_actor(db)
    checks.next_check = _check_row(db, actor, "msa", status="queued")
    service = DraftingTemplateService(db)

    result = service.save(actor=actor, key="msa", body=default_body("msa") + "\n\nEdit.", note=None)

    s.TemplateMutationResultOut.model_validate(result)
    assert result["check"]["id"] == checks.next_check.id and result["check"]["state"] == "queued"
    assert result["template"]["version"] == 1


# --- What the API shows --------------------------------------------------------------------------


def test_list_get_and_versions_match_the_frozen_shapes(db):
    actor = _make_actor(db)
    pb = _playbook(db, actor, "Non-Disclosure Playbook")
    service = DraftingTemplateService(db)
    service.save(actor=actor, key="nda", body=default_body("nda") + "\n\nEdit {company}.", note="n")
    _pin_row(db, actor, "nda", pb)

    listed = service.list(org_id=actor.org_id)
    detail = service.get(org_id=actor.org_id, key="nda")
    versions = service.versions(org_id=actor.org_id, key="nda")

    assert [t["key"] for t in listed] == list(TEMPLATES)
    for item in listed:
        s.DraftingTemplateSummaryOut.model_validate(item)
    s.DraftingTemplateOut.model_validate(detail)
    for v in versions:
        s.DraftingTemplateVersionOut.model_validate(v)
    assert detail["binding"] == {"form_key": "new_agreement", "form_name": "New agreement",
                                 "agreement_kind": "NDA", "label": "New agreement · NDA"}
    assert detail["playbook"] == {"id": pb.id, "name": pb.name, "version_id": detail["playbook"]["version_id"],
                                  "version_number": 1, "pinned": True, "available": True}
    assert detail["default_body"] == default_body("nda") and detail["editable"] is True
    assert detail["updated_by"] == "admin" and detail["updated_at"] is not None
    assert {"name": "company", "label": "Our entity", "place": "body",
            "count": detail["body"].count("{company}")} in detail["fields"]
    untouched = next(t for t in listed if t["key"] == "msa")
    assert untouched["version"] == 0 and untouched["updated_at"] is None and untouched["source"] == "default"
    assert [v["version"] for v in versions] == [1, 0]
    assert versions[-1]["body"] == default_body("nda") and versions[-1]["created_at"] is None


def test_each_version_lists_only_its_own_saved_checks(db):
    """FR-57: a version shows the results recorded against it — not an unsaved editor check, not
    another version's, not another template's or organisation's."""
    actor = _make_actor(db)
    stranger = _make_actor(db)
    service = DraftingTemplateService(db)
    service.save(actor=actor, key="msa", body=default_body("msa") + "\n\nEdit.", note=None)
    on_v0 = _check_row(db, actor, "msa", version=0)
    on_v1 = _check_row(db, actor, "msa", version=1, findings=_FINDINGS)
    _check_row(db, actor, "msa", version=1, unsaved=True)
    _check_row(db, actor, "nda", version=1)
    _check_row(db, stranger, "msa", version=1, org_id=stranger.org_id)

    versions = {v["version"]: v for v in service.versions(org_id=actor.org_id, key="msa")}

    assert [c["id"] for c in versions[0]["checks"]] == [on_v0.id]
    assert [c["id"] for c in versions[1]["checks"]] == [on_v1.id]
    assert versions[1]["checks"][0]["finding_count"] == 2


def test_another_organisations_wording_and_pin_are_invisible(db):
    """FR-69: org A's saved wording, versions and pin must not leak into org B."""
    a, b = _make_actor(db), _make_actor(db)
    pb = _playbook(db, a, "Services Agreement Playbook")
    service = DraftingTemplateService(db)
    service.set_playbook(actor=a, key="msa", playbook_id=pb.id)
    service.save(actor=a, key="msa", body=default_body("msa") + "\n\nOnly A.", note=None)

    assert service.body_for(org_id=b.org_id, key="msa") == default_body("msa")
    assert [v["version"] for v in service.versions(org_id=b.org_id, key="msa")] == [0]
    assert get_type(db, b.org_id, "msa").playbook_id is None
    assert service.get(org_id=b.org_id, key="msa")["playbook"] is None
    assert service.options(org_id=b.org_id)["playbooks"] == []
    assert current_wording(db, b.org_id, get_type(db, b.org_id, "msa")).version == 0


def test_options_offer_published_playbooks_and_who_holds_each_binding(db):
    actor = _make_actor(db)
    stranger = _make_actor(db)
    customer = _playbook(db, actor, "Customer Agreement Playbook")
    second = _playbook(db, actor, "Beta Playbook")
    db.add(PlaybookVersion(org_id=actor.org_id, playbook_id=second.id, version_number=2,
                           status=PlaybookStatus.PUBLISHED))
    _playbook(db, actor, "Deleted Playbook", deleted=True)
    _playbook(db, actor, "Draft Only Playbook", published=False)
    _playbook(db, stranger, "Someone Else's Playbook")
    db.flush()

    options = DraftingTemplateService(db).options(org_id=actor.org_id)

    s.TemplateTypeOptionsOut.model_validate(options)
    assert options["playbooks"] == [{"id": second.id, "name": "Beta Playbook", "version_number": 2},
                                    {"id": customer.id, "name": "Customer Agreement Playbook",
                                     "version_number": 1}]
    by_label = {b["label"]: b for b in options["bindings"]}
    assert len(options["bindings"]) == len(CONTRACT_TYPE_OF_KIND) + len(CONTRACT_TYPE_OF_FORM)
    assert by_label["New agreement · NDA"]["held_by"] == {"key": "nda", "name": "Non-Disclosure Agreement"}
    assert by_label["Statement of Work"]["held_by"] == {"key": "sow", "name": "Statement of Work"}
    cust = by_label["New agreement · Selling to a customer"]
    assert cust["held_by"] is None and cust["contract_type"] == "Customer Agreement"
    assert cust["suggested_playbook"] == {"id": customer.id, "name": "Customer Agreement Playbook"}
    assert by_label["Data Processing Agreement"]["suggested_playbook"] is None


def test_list_types_overlays_the_org_rows_on_the_catalog(db):
    """A row only adds the org's choices to a shipped type; the shipped facts stay the code's, and a
    retired row stops the type being offered unless asked for."""
    actor = _make_actor(db)
    pb = _playbook(db, actor, "Services Agreement Playbook")
    _pin_row(db, actor, "msa", pb)
    db.add(DraftingTemplateType(
        org_id=actor.org_id, key="customer", name="Customer Agreement", label="Customer Agreement",
        form_key="new_agreement", agreement_kind="Selling to a customer", shipped=False, status="retired"))
    db.flush()

    types = {t.key: t for t in list_types(db, actor.org_id)}
    assert list(types) == list(TEMPLATES)
    assert types["msa"].playbook_id == pb.id and types["nda"].playbook_id is None
    assert all(t.shipped and t.active for t in types.values())
    with_retired = {t.key: t for t in list_types(db, actor.org_id, include_retired=True)}
    assert with_retired["customer"].active is False and with_retired["customer"].shipped is False
    assert with_retired["customer"].contract_type == "Customer Agreement"
    assert get_type(db, actor.org_id, "customer").key == "customer"
    with pytest.raises(HTTPException):
        get_type(db, new_uuid(), "customer")


def test_an_edited_default_changes_the_wording_hash_of_version_zero(db, monkeypatch, tmp_path):
    """A release that edits defaults/<key>.txt must show as a change to a never-edited template, or
    its last check would keep claiming the wording it read is the wording now."""
    actor = _make_actor(db)
    ttype = get_type(db, actor.org_id, "nda")
    before = current_wording(db, actor.org_id, ttype)
    assert before.version == 0 and before.sha256 == body_sha256(default_body("nda"))
    shutil.copytree(svc._DEFAULTS, tmp_path / "defaults")
    nda = tmp_path / "defaults" / "nda.txt"
    nda.write_text(nda.read_text(encoding="utf-8") + "\nA release added this sentence.\n", encoding="utf-8")
    monkeypatch.setattr(svc, "_DEFAULTS", tmp_path / "defaults")
    default_body.cache_clear()
    try:
        after = current_wording(db, actor.org_id, ttype)
    finally:
        default_body.cache_clear()
    assert after.version == 0 and after.sha256 != before.sha256 and "A release added" in after.body


# --- The endpoints: permissions, scoping, shapes ------------------------------------------------
#
# The service is covered above. Here the routes themselves are driven, which is where a missing
# permission gate, a route captured by ``/{key}`` or a payload field that never reaches the service
# would hide.

API = "/api/v1/drafting-templates"
READ_RUN = ["playbook:read", "playbook:run"]  # what a legal reviewer has: view and run, not edit
EDITOR = ["playbook:read", "playbook:update"]


@pytest.fixture
def api(db):
    from fastapi.testclient import TestClient

    from app.core.rate_limit import limiter
    from app.main import app

    def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    previous = limiter.enabled
    limiter.enabled = False
    try:
        with TestClient(app) as client:
            yield client
    finally:
        limiter.enabled = previous
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def _as(user: User) -> None:
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user


def _role_user(db: Session, permissions: list[str], name: str = "admin") -> User:
    """A user in a new organisation whose only role grants exactly ``permissions``."""
    org = Organization(id=new_uuid(), name="roles", slug=f"roles-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    unit = OrgUnit(org_id=org.id, name="HQ", parent_id=None)
    db.add(unit)
    role = Role(org_id=org.id, name=f"role-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=True)
    for value in permissions:
        perm = db.query(Permission).filter(Permission.value == value).one_or_none()
        if perm is None:
            perm = Permission(value=value)
            db.add(perm)
        role.permissions.append(perm)
    db.add(role)
    user = _make_user(db, org.id, name)
    db.flush()
    db.add(UserRoleGrant(user_id=user.id, role_id=role.id, org_id=org.id, org_unit_id=unit.id))
    db.flush()
    return user


_NOTE = {"note": "x", "expected_version": 0}
_CHANGES = [  # every route that changes a template
    ("put", "/msa", {"body": "{company} and {counterparty}.", "expected_version": 0}),
    ("post", "/msa/reset", _NOTE),
    ("post", "/msa/versions/1/restore", _NOTE),
    ("put", "/msa/playbook", {"playbook_id": "anything"}),
]
_CHANGE_IDS = ["save", "reset", "restore", "change playbook"]


@pytest.mark.parametrize(("method", "path", "payload"), _CHANGES, ids=_CHANGE_IDS)
def test_a_person_who_may_view_and_run_but_not_edit_cannot_change_a_template(
        api, db, checks, method, path, payload):
    """A legal reviewer may read and check templates but never change our standard paper (FR-68).
    The refusal must come before anything is written, requested or paid for."""
    user = _role_user(db, READ_RUN, "reviewer")
    _as(user)

    r = getattr(api, method)(API + path, json=payload)

    assert r.status_code == 403, r.text
    assert r.json()["detail"] == f"Missing permission: {'playbook:update'}"
    assert _count(db, DraftingTemplateVersion, user.org_id) == 0
    assert _count(db, DraftingTemplateType, user.org_id) == 0
    assert _count(db, DraftingTemplateCheck, user.org_id) == 0
    assert checks.requests == [] and _audit(db, user.org_id, "drafting_template.saved") == []


@pytest.mark.parametrize(("method", "path", "payload"), _CHANGES, ids=_CHANGE_IDS)
def test_a_person_with_no_playbook_permission_at_all_cannot_change_a_template(api, db, method, path, payload):
    user = _role_user(db, ["contract:read"])
    _as(user)
    assert getattr(api, method)(API + path, json=payload).status_code == 403


@pytest.mark.parametrize(("method", "path", "payload"), [
    ("get", "", None), ("get", "/msa", None), ("get", "/msa/versions", None), ("get", "/options", None),
    ("post", "/msa/preview", {"body": "{company}"}),
], ids=["list", "get", "versions", "options", "preview"])
def test_reading_a_template_needs_the_permission_to_view_playbooks(api, db, method, path, payload):
    """The templates are our standard paper: running or editing playbooks without being allowed to
    view them must not open the page's data (FR-66)."""
    user = _role_user(db, ["playbook:run", "playbook:update"])
    _as(user)
    r = getattr(api, method)(API + path, **({"json": payload} if payload else {}))
    assert r.status_code == 403 and r.json()["detail"] == "Missing permission: playbook:read"


def test_the_routes_use_exactly_the_permissions_the_application_already_had():
    """AC-70: this feature adds no permission. Every route is held to the one the plan names, each of
    them is an existing playbook permission, and the application's own list is the same as before."""
    from app.core.rbac import (
        ALL_PERMISSIONS,
        DEFAULT_ROLE_PERMISSIONS,
        LEGAL_REVIEWER_ROLE_NAME,
        PLAYBOOK_PERMISSIONS,
    )
    from app.main import app

    found: dict[tuple[str, str], str] = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith(API):
            continue
        (gate,) = [d.call for d in route.dependant.dependencies if "permission" in d.call.__code__.co_freevars]
        permission = dict(zip(gate.__code__.co_freevars, (c.cell_contents for c in gate.__closure__),
                              strict=True))["permission"]
        for method in route.methods:
            found[(method, path[len(API):] or "/")] = permission

    assert found == {
        ("GET", "/"): "playbook:read", ("GET", "/options"): "playbook:read",
        ("POST", "/check-all"): "playbook:run", ("GET", "/{key}"): "playbook:read",
        ("GET", "/{key}/versions"): "playbook:read", ("POST", "/{key}/preview"): "playbook:read",
        ("POST", "/{key}/checks"): "playbook:run", ("GET", "/{key}/checks"): "playbook:read",
        ("GET", "/{key}/checks/{check_id}"): "playbook:read", ("PUT", "/{key}"): "playbook:update",
        ("POST", "/{key}/reset"): "playbook:update",
        ("POST", "/{key}/versions/{version}/restore"): "playbook:update",
        ("PUT", "/{key}/playbook"): "playbook:update",
        ("POST", "/{key}/playbook/standard"): "playbook:publish",
    }
    assert set(found.values()) <= PLAYBOOK_PERMISSIONS <= ALL_PERMISSIONS
    assert PLAYBOOK_PERMISSIONS == {"playbook:read", "playbook:create", "playbook:update", "playbook:delete",
                                    "playbook:run", "playbook:publish"}
    reviewer = DEFAULT_ROLE_PERMISSIONS[LEGAL_REVIEWER_ROLE_NAME]
    assert {"playbook:read", "playbook:run"} <= reviewer and not {"playbook:update", "playbook:publish"} & reviewer


def test_the_static_paths_come_before_the_template_key_in_the_route_table():
    """FastAPI matches in order: a ``/{key}`` registered first would take GET /options and the
    Change playbook picker would get 'No such template'."""
    from app.main import app

    paths = [(m, r.path) for r in app.routes if getattr(r, "path", "").startswith(API) for m in r.methods]
    assert paths.index(("GET", f"{API}/options")) < paths.index(("GET", f"{API}/{{key}}"))
    assert paths.index(("POST", f"{API}/check-all")) < paths.index(("POST", f"{API}/{{key}}/reset"))


def test_an_editor_can_save_reset_restore_and_change_the_playbook_through_the_api(api, db, checks):
    """The allowed half of AC-70, and that each payload field reaches the service (a field dropped by
    the route would silently disable the stale-page guard or the parties confirmation)."""
    user = _role_user(db, EDITOR)
    pb = _playbook(db, user, "Services Agreement Playbook")
    _as(user)
    a = default_body("msa") + "\n\nA {company}."
    b = default_body("msa") + "\n\nB {company}."

    saved = api.put(f"{API}/msa", json={"body": a, "note": "first", "expected_version": 0})
    assert saved.status_code == 200, saved.text
    assert set(saved.json()) == {"template", "changed", "check"}
    assert saved.json()["changed"] is True and saved.json()["template"]["version"] == 1
    assert saved.json()["check"] is None  # no check was started (the stand-in answers none)
    assert checks.requests == [{"org_id": user.org_id, "key": "msa", "trigger": "saved", "actor_user_id": user.id}]
    assert api.put(f"{API}/msa", json={"body": b, "expected_version": 1}).json()["template"]["version"] == 2

    restored = api.post(f"{API}/msa/versions/1/restore", json={"note": "back", "expected_version": 2})
    assert restored.status_code == 200 and restored.json()["template"]["version"] == 3
    assert restored.json()["template"]["body"] == a

    reset = api.post(f"{API}/msa/reset", json={"expected_version": 3})
    assert reset.status_code == 200 and reset.json()["template"]["source"] == "default"

    pinned = api.put(f"{API}/msa/playbook", json={"playbook_id": pb.id, "note": "use this"})
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["changed"] is True and pinned.json()["template"]["playbook"]["id"] == pb.id
    assert [r["trigger"] for r in checks.requests] == ["saved", "saved", "restored", "reset", "playbook_changed"]


def test_a_save_made_on_a_stale_page_is_a_409_through_the_api(api, db):
    """``expected_version`` is required and enforced on every change route (FR-59): without it a
    colleague's newer version would be overwritten silently."""
    user = _role_user(db, EDITOR, "Priya")
    _as(user)
    body = default_body("vendor") + "\n\nSomeone's edit {counterparty}."
    assert api.put(f"{API}/vendor", json={"body": body, "expected_version": 0}).status_code == 200

    stale = api.put(f"{API}/vendor", json={"body": default_body("vendor") + "\n\nMine {company}.",
                                           "expected_version": 0})
    assert stale.status_code == 409 and stale.json()["detail"].startswith("Priya saved version 1 of this template at ")
    assert api.post(f"{API}/vendor/reset", json=_NOTE).status_code == 409
    assert api.post(f"{API}/vendor/versions/1/restore", json=_NOTE).status_code == 409
    missing = api.put(f"{API}/vendor", json={"body": body})
    assert missing.status_code == 422, "a save that does not say which version it was made on must be refused"


def test_wording_with_no_party_field_needs_the_yes_to_save_through_the_api(api, db):
    user = _role_user(db, EDITOR)
    _as(user)
    plain = "This agreement stands alone."

    refused = api.put(f"{API}/dpa", json={"body": plain, "expected_version": 0})
    assert refused.status_code == 422 and "no field for our entity or the other party" in refused.json()["detail"]
    assert _count(db, DraftingTemplateVersion, user.org_id) == 0
    ok = api.put(f"{API}/dpa", json={"body": plain, "expected_version": 0, "acknowledge_missing_parties": True})
    assert ok.status_code == 200 and ok.json()["template"]["version"] == 1


def test_a_suggestion_that_is_not_from_a_check_of_this_template_is_refused_through_the_api(api, db):
    """The applied-findings list reaches the service, which looks each one up in this org's checks."""
    user = _role_user(db, EDITOR)
    other = _role_user(db, EDITOR)
    foreign = _check_row(db, other, "msa", findings=_FINDINGS)
    _as(user)

    r = api.put(f"{API}/msa", json={"body": default_body("msa") + "\n\nX {company}.", "expected_version": 0,
                                   "applied_findings": [{"check_id": foreign.id, "finding_index": 0}]})

    assert r.status_code == 422 and r.json()["detail"] == _NOT_OURS
    assert _count(db, DraftingTemplateVersion, user.org_id) == 0


def test_an_unknown_template_is_a_404_on_every_route(api, db):
    user = _role_user(db, EDITOR + ["playbook:run"])
    _as(user)
    calls = [
        api.get(f"{API}/customer"), api.get(f"{API}/customer/versions"),
        api.put(f"{API}/customer", json={"body": "{company} {counterparty}", "expected_version": 0}),
        api.post(f"{API}/customer/reset", json=_NOTE),
        api.post(f"{API}/customer/versions/1/restore", json=_NOTE),
        api.put(f"{API}/customer/playbook", json={"playbook_id": "x"}),
    ]
    assert [c.status_code for c in calls] == [404] * 6
    assert {c.json()["detail"] for c in calls} == {"No such template"}


def test_another_organisations_versions_and_playbooks_are_not_reachable_through_the_api(api, db):
    """FR-69 / AC-63: org A's saved wording and playbook must read as missing to org B, and B must
    not be able to pin A's playbook to its own template."""
    a = _role_user(db, EDITOR)
    b = _role_user(db, EDITOR)
    pb = _playbook(db, a, "Services Agreement Playbook")
    _as(a)
    assert api.put(f"{API}/msa", json={"body": default_body("msa") + "\n\nOnly A {company}.",
                                       "expected_version": 0}).status_code == 200
    _as(b)

    assert [v["version"] for v in api.get(f"{API}/msa/versions").json()] == [0]
    assert "Only A" not in api.get(f"{API}/msa").json()["body"]
    restore = api.post(f"{API}/msa/versions/1/restore", json={"expected_version": 0})
    assert restore.status_code == 404 and restore.json()["detail"] == "No such version of this template."
    pin = api.put(f"{API}/msa/playbook", json={"playbook_id": pb.id})
    assert pin.status_code == 422 and pin.json()["detail"] == _PICK
    assert api.get(f"{API}/options").json()["playbooks"] == []
    assert _count(db, DraftingTemplateVersion, b.org_id) == 0 and get_type(db, b.org_id, "msa").playbook_id is None


def test_the_api_answers_in_the_frozen_shapes(api, db):
    """``response_model`` is set on every read: a service that drifts from the contract fails here
    (a 500 from the response check) instead of in the page."""
    user = _role_user(db, EDITOR)
    _as(user)

    listed = api.get(API)
    assert listed.status_code == 200 and [t["key"] for t in listed.json()] == list(TEMPLATES)
    assert all(t["status"]["state"] == "never_checked" for t in listed.json())
    assert len(api.get(f"{API}?include_retired=true").json()) == len(TEMPLATES)
    detail = api.get(f"{API}/nda").json()
    assert detail["body"] == default_body("nda") and detail["default_body"] == default_body("nda")
    assert [v["version"] for v in api.get(f"{API}/nda/versions").json()] == [0]
    options = api.get(f"{API}/options")
    assert options.status_code == 200 and set(options.json()) == {"bindings", "playbooks"}
    preview = api.post(f"{API}/nda/preview", json={"body": "Between {company} and {counterparty}."})
    assert preview.json() == {"text": "Between Aegis Pharma Ltd and Globex Corporation."}
    bad = api.post(f"{API}/nda/preview", json={"body": "Dear {client_name},"})
    assert bad.status_code == 422 and "Unknown placeholder" in bad.json()["detail"]
