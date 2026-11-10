"""Pins the drafting-templates API shapes frozen in specs/006-template-playbook-sync/plan.md.

The frontend copies these shapes into ``types.ts`` in parallel, so a renamed field or a
Literal value added on one side only is the failure these tests exist to catch.
"""

from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from app.drafting_templates import schemas as s

# --- The closed vocabularies, copied from plan.md "Frontend TypeScript models" ---------------

LITERALS = {
    "TemplateSource": ["default", "text", "docx", "pdf", "scan"],
    # FR-2: the spec's eight statuses, and only those.
    "TemplateStatusState": [
        "passes",
        "has_findings",
        "checking",
        "out_of_date",
        "partly_checked",
        "couldnt_check",
        "never_checked",
        "no_playbook",
    ],
    "TemplateCheckTrigger": [
        "saved",
        "uploaded",
        "restored",
        "reset",
        "type_created",
        "type_reactivated",
        "playbook_published",
        "playbook_changed",
        "manual",
        "check_all",
        "retry",
        "sample",  # spec 014: the check run on the sample wording
    ],
    "TemplateCheckState": ["queued", "running", "completed", "failed", "superseded"],
    "TemplateCheckError": [
        "ai_unavailable",
        "cost_cap",
        "no_rules",
        "no_playbook",
        "unfillable",
        "wording_changed",
        "interrupted",
    ],
    "FindingSeverity": ["low", "medium", "high", "critical"],
    "TemplatePlace": ["body", "table", "header", "footer", "text_box", "footnote", "endnote"],
    "StatusChanged": ["wording", "playbook", "both"],
    "CheckAllSkipReason": ["checking", "no_playbook"],
}


@pytest.mark.parametrize("name", sorted(LITERALS))
def test_literal_union_equals_plan(name):
    """A value added or dropped here but not in plan.md/types.ts makes the page render a
    status or error code its own type says cannot exist (FR-2, FR-18)."""
    assert list(get_args(getattr(s, name))) == LITERALS[name]


# --- Field names, copied from the TS interfaces ------------------------------------------------

SUMMARY = {
    "id", "state", "trigger", "unsaved", "template_version", "playbook_name",
    "playbook_version_number", "finding_count", "highest_severity", "text_was_truncated",
    "error_code", "error_message", "requested_by", "on_behalf", "created_at", "started_at",
    "finished_at",
}
CHECK_ONLY = {
    "template_key", "trigger_detail", "wording_sha256", "playbook_id", "playbook_version_id",
    "rule_count", "findings", "summary", "text_length", "read_length",
}
TEMPLATE_SUMMARY = {
    "key", "name", "label", "contract_type", "binding", "playbook", "shipped", "active",
    "source", "version", "original_filename", "updated_at", "updated_by", "editable", "status",
    "standard",  # spec 014: whether wording / rules predate the standard
}

FIELDS = {
    s.TemplateCheckSummaryOut: SUMMARY,
    s.TemplateCheckOut: SUMMARY | CHECK_ONLY,
    s.TemplateCheckFindingOut: {
        "index", "rule_index", "clause_type", "severity", "issue", "quote", "quote_template",
        "suggested_fix", "suggested_fix_template", "suggestion_is_wording", "approval_required",
    },
    s.TemplateStatusOut: {
        "state", "label", "reason", "finding_count", "highest_severity", "changed", "error_code",
        "results_vary", "decided_by", "last_completed", "active_check",
    },
    s.TemplateBindingOut: {"form_key", "form_name", "agreement_kind", "label"},
    s.TemplatePlaybookRefOut: {"id", "name", "version_id", "version_number", "pinned", "available"},
    s.TemplateFileRefOut: {"id", "filename", "mime_type", "size_bytes"},
    s.DraftingTemplateSummaryOut: TEMPLATE_SUMMARY,
    s.DraftingTemplateOut: TEMPLATE_SUMMARY
    | {"body", "default_body", "placeholders", "fields", "original_file"},
    s.DraftingTemplatePlaceholderOut: {"name", "label", "about", "sample"},
    s.TemplateFieldUseOut: {"name", "label", "place", "count"},
    s.AppliedFindingOut: {"check_id", "finding_index", "clause_type", "severity"},
    s.DraftingTemplateVersionOut: {
        "version", "note", "source", "is_default", "body", "created_at", "created_by",
        "original_file", "applied_findings", "restored_from_version", "ocr_confirmed_by",
        "ocr_confirmed_at", "checks",
    },
    s.TemplateMutationResultOut: {"template", "changed", "check"},
    s.CheckAllResultOut: {"started", "skipped"},
    s.TemplateBindingOptionOut: {
        "form_key", "agreement_kind", "label", "contract_type", "held_by", "suggested_playbook",
    },
    s.TemplateTypeOptionsOut: {"bindings", "playbooks"},
    s.TemplateSaveRequest: {
        "body", "note", "expected_version", "applied_findings", "acknowledge_missing_parties",
    },
    s.TemplateVersionAction: {"note", "expected_version"},
    s.TemplateCheckRequest: {"body"},
    s.TemplatePlaybookChange: {"playbook_id", "note"},
}


@pytest.mark.parametrize("model", list(FIELDS), ids=lambda m: m.__name__)
def test_field_names_equal_plan(model):
    """A renamed, dropped or extra field is invisible to the backend tests but breaks the
    page that reads the TypeScript copy of the same shape."""
    assert set(model.model_fields) == FIELDS[model]


def test_summary_is_the_full_check_without_the_heavy_fields():
    """The list rows and the version history carry the summary; if a finding list or the
    wording hash leaks into it, every status payload gets heavy (FR-18 split)."""
    assert not set(s.TemplateCheckSummaryOut.model_fields) & CHECK_ONLY
    assert set(s.TemplateCheckOut.model_fields) - set(s.TemplateCheckSummaryOut.model_fields) == (
        CHECK_ONLY
    )


# --- Request limits and required fields --------------------------------------------------------


def test_save_requires_expected_version():
    """FR-59: a save that does not say which version it loaded could overwrite a
    colleague's newer version without a conflict."""
    with pytest.raises(ValidationError):
        s.TemplateSaveRequest(body="x")


def test_save_defaults_are_the_safe_ones():
    """Findings default empty and the missing-parties confirmation defaults off, so
    nothing is applied or waved through unless the person asked for it (FR-35)."""
    req = s.TemplateSaveRequest(body="x", expected_version=0)
    assert req.applied_findings == []
    assert req.acknowledge_missing_parties is False
    assert req.note is None


def test_expected_version_cannot_be_negative():
    """0 means never saved; a negative number is a broken client, not a version."""
    with pytest.raises(ValidationError):
        s.TemplateSaveRequest(body="x", expected_version=-1)
    with pytest.raises(ValidationError):
        s.TemplateVersionAction(expected_version=-1)


def test_version_action_requires_expected_version():
    """Reset and restore are mutations too; without the version they would skip the
    conflict check (FR-59)."""
    with pytest.raises(ValidationError):
        s.TemplateVersionAction(note="back")


def test_body_limit_is_200000_characters():
    """Wording is stored and sent to the AI review whole; the cap keeps one request from
    holding megabytes."""
    s.TemplateSaveRequest(body="x" * 200_000, expected_version=0)
    s.TemplatePreviewRequest(body="x" * 200_000)
    s.TemplateCheckRequest(body="x" * 200_000)
    with pytest.raises(ValidationError):
        s.TemplateSaveRequest(body="x" * 200_001, expected_version=0)
    with pytest.raises(ValidationError):
        s.TemplatePreviewRequest(body="x" * 200_001)
    with pytest.raises(ValidationError):
        s.TemplateCheckRequest(body="x" * 200_001)


def test_note_limit_is_300_characters():
    """The note lands in the version history and the audit row; unbounded text there
    would bloat both."""
    s.TemplateSaveRequest(body="x", note="n" * 300, expected_version=0)
    with pytest.raises(ValidationError):
        s.TemplateSaveRequest(body="x", note="n" * 301, expected_version=0)
    with pytest.raises(ValidationError):
        s.TemplateVersionAction(note="n" * 301, expected_version=0)
    with pytest.raises(ValidationError):
        s.TemplatePlaybookChange(playbook_id="p", note="n" * 301)


def test_check_request_body_is_optional():
    """An empty body means 'check the saved wording'; requiring it would make the page
    send the editor text for every check."""
    assert s.TemplateCheckRequest().body is None
    assert s.TemplateCheckRequest.model_validate({}).body is None


def test_playbook_change_requires_playbook_id():
    """Pinning nothing is not a change; the playbook has to be named."""
    with pytest.raises(ValidationError):
        s.TemplatePlaybookChange()


def test_applied_finding_needs_check_and_index():
    """A suggestion is identified by its check and its position; either alone points at
    nothing (FR-52 provenance)."""
    with pytest.raises(ValidationError):
        s.TemplateSaveRequest(
            body="x", expected_version=0, applied_findings=[{"check_id": "c"}]
        )


# --- Responses: the plan's example payloads round-trip -----------------------------------------

CHECK_SUMMARY = {
    "id": "c1", "state": "completed", "trigger": "saved", "unsaved": False,
    "template_version": 4, "playbook_name": "Master Services Agreement Playbook",
    "playbook_version_number": 3, "finding_count": 2, "highest_severity": "high",
    "text_was_truncated": False, "error_code": None, "error_message": None,
    "requested_by": "Priya Shah", "on_behalf": True,
    "created_at": "2026-10-15T07:30:00Z", "started_at": "2026-10-15T07:30:01Z",
    "finished_at": "2026-10-15T07:30:20Z",
}
FINDING = {
    "index": 0, "rule_index": 4, "clause_type": "service_levels", "severity": "medium",
    "issue": "No uptime commitment.", "quote": "filled text", "quote_template": "text {codes}",
    "suggested_fix": "Add 99.9%.", "suggested_fix_template": "Add {payment_days}.",
    "suggestion_is_wording": True, "approval_required": False,
}
CHECK = CHECK_SUMMARY | {
    "template_key": "msa",
    "trigger_detail": {"playbook_id": "p1", "playbook_version_id": "pv1", "version_number": 3},
    "wording_sha256": "a" * 64, "playbook_id": "p1", "playbook_version_id": "pv1",
    "rule_count": 9, "findings": [FINDING], "summary": "Two issues.",
    "text_length": 25270, "read_length": 25270,
}
STATUS = {
    "state": "has_findings", "label": "Has findings", "reason": "2 findings, highest High.",
    "finding_count": 2, "highest_severity": "high", "changed": None, "error_code": None,
    "results_vary": False, "decided_by": CHECK_SUMMARY, "last_completed": CHECK_SUMMARY,
    "active_check": None,
}
TEMPLATE_SUMMARY_PAYLOAD = {
    "key": "nda", "name": "Non-Disclosure Agreement", "label": "Mutual NDA",
    "contract_type": "NDA",
    "binding": {
        "form_key": "new_agreement", "form_name": "New agreement", "agreement_kind": "NDA",
        "label": "New agreement · NDA",
    },
    "playbook": {
        "id": "p1", "name": "NDA Playbook", "version_id": "pv1", "version_number": 2,
        "pinned": True, "available": True,
    },
    "shipped": True, "active": True, "source": "default", "version": 0,
    "original_filename": None, "updated_at": None, "updated_by": None, "editable": True,
    "status": STATUS,
    "standard": {
        "revision": "014", "updated_on": "2026-10-27", "wording_behind": False,
        "playbook_behind": True,
    },
}
TEMPLATE = TEMPLATE_SUMMARY_PAYLOAD | {
    "body": "Between {company} and {counterparty}.", "default_body": None,
    "placeholders": [
        {
            "name": "counterparty", "label": "The other party", "about": "Who signs with us.",
            "sample": "Globex Corporation",
        }
    ],
    "fields": [{"name": "counterparty", "label": "The other party", "place": "footer", "count": 1}],
    "original_file": {
        "id": "f1", "filename": "MSA.docx", "mime_type": "application/octet-stream",
        "size_bytes": 81234,
    },
}
VERSION = {
    "version": 4, "note": "tightened", "source": "text", "is_default": False, "body": "x",
    "created_at": "2026-10-15T07:30:00Z", "created_by": "Priya Shah", "original_file": None,
    "applied_findings": [
        {"check_id": "c1", "finding_index": 0, "clause_type": "payment_terms", "severity": "high"}
    ],
    "restored_from_version": 2, "ocr_confirmed_by": None, "ocr_confirmed_at": None,
    "checks": [CHECK_SUMMARY],
}
OPTIONS = {
    "bindings": [
        {
            "form_key": "new_agreement", "agreement_kind": "Selling to a customer",
            "label": "New agreement · Selling to a customer",
            "contract_type": "Customer Agreement", "held_by": None,
            "suggested_playbook": {"id": "p1", "name": "Customer Agreement Playbook"},
        },
        {
            "form_key": "sow", "agreement_kind": None, "label": "Statement of work",
            "contract_type": "SOW", "held_by": {"key": "sow", "name": "SOW"},
            "suggested_playbook": None,
        },
    ],
    "playbooks": [{"id": "p1", "name": "Customer Agreement Playbook", "version_number": 1}],
}

PAYLOADS = [
    (s.TemplateCheckSummaryOut, CHECK_SUMMARY),
    (s.TemplateCheckOut, CHECK),
    (s.TemplateStatusOut, STATUS),
    (s.DraftingTemplateSummaryOut, TEMPLATE_SUMMARY_PAYLOAD),
    (s.DraftingTemplateOut, TEMPLATE),
    (s.DraftingTemplateVersionOut, VERSION),
    (s.TemplateMutationResultOut, {"template": TEMPLATE, "changed": True, "check": CHECK_SUMMARY}),
    (s.TemplateMutationResultOut, {"template": TEMPLATE, "changed": False, "check": None}),
    (
        s.CheckAllResultOut,
        {
            "started": [{"key": "msa", "check_id": "c1"}],
            "skipped": [
                {"key": "nda", "reason": "checking"},
                {"key": "dpa", "reason": "no_playbook"},
            ],
        },
    ),
    (s.TemplateTypeOptionsOut, OPTIONS),
]


@pytest.mark.parametrize(
    ("model", "payload"), PAYLOADS, ids=[f"{m.__name__}-{i}" for i, (m, _) in enumerate(PAYLOADS)]
)
def test_plan_payload_round_trips(model: type[BaseModel], payload):
    """A payload shaped exactly as plan.md shows must pass the response model unchanged;
    if it did not, the route's response_model would 500 on the very shape the page expects."""
    assert model.model_validate(payload).model_dump(mode="json") == payload


@pytest.mark.parametrize(
    ("model", "payload", "key"),
    [
        (s.TemplateCheckSummaryOut, CHECK_SUMMARY, "finished_at"),
        (s.TemplateStatusOut, STATUS, "active_check"),
        (s.DraftingTemplateSummaryOut, TEMPLATE_SUMMARY_PAYLOAD, "playbook"),
        (s.TemplateMutationResultOut, {"template": TEMPLATE, "changed": True, "check": None}, "check"),
    ],
)
def test_nullable_keys_must_still_be_present(model, payload, key):
    """The contract sends null, never an absent key; a service that drops the key would
    pass a lenient model and leave the page reading undefined."""
    broken = {k: v for k, v in payload.items() if k != key}
    with pytest.raises(ValidationError):
        model.model_validate(broken)


@pytest.mark.parametrize(
    ("model", "payload", "key", "bad"),
    [
        (s.TemplateStatusOut, STATUS, "state", "unknown"),
        (s.TemplateStatusOut, STATUS, "changed", "nothing"),
        (s.TemplateCheckSummaryOut, CHECK_SUMMARY, "state", "done"),
        (s.TemplateCheckSummaryOut, CHECK_SUMMARY, "trigger", "cron"),
        (s.TemplateCheckSummaryOut, CHECK_SUMMARY, "error_code", "oops"),
        (s.TemplateCheckSummaryOut, CHECK_SUMMARY, "highest_severity", "severe"),
        (s.DraftingTemplateVersionOut, VERSION, "source", "email"),
        (s.TemplateFieldUseOut, TEMPLATE["fields"][0], "place", "margin"),
    ],
)
def test_out_of_vocabulary_value_is_rejected(model, payload, key, bad):
    """A status, trigger or error code the page has no label for must fail in the API
    response, not render as a blank badge."""
    with pytest.raises(ValidationError):
        model.model_validate({**payload, key: bad})
