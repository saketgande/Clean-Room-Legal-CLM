"""The five read tools. Failure modes guarded: a guessed triage result while Aegis is still
working, SLA data reshaped on the way out, extracted document text leaking by default, a REQ
reference resolving to the wrong (or someone else's) request, and a failed read reported as
data."""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest
from conftest import KEY, FakePlatform

from aegis_intake_mcp.errors import ToolError
from aegis_intake_mcp.tools import ToolContext, call_tool


@pytest.fixture
def ctx(client, config) -> ToolContext:
    return ToolContext(client=client, config=config, headers={"authorization": KEY})


def req(**over: Any) -> dict[str, Any]:
    base = {
        "id": "id-1",
        "ref": "REQ-1042",
        "subject": "Acme NDA",
        "description": "Mutual NDA for pilot",
        "type_label": "New agreement · NDA",
        "field_values": {"request_form": "new_agreement"},
        "status": "open",
        "stage": "new",
        "assigned_to_label": None,
        "sla_status": "on_track",
        "submitted_at": "2026-10-21T10:00:00+00:00",
        "ai_triage": {"status": "pending"},
        "workflow": [],
    }
    base.update(over)
    return base


COMPLETE = {
    "status": "complete",
    "category": "NDA",
    "needs_info": False,
    "flow_suggestion": {"flow_name": "NDA fast lane"},
}

FORMS = [
    {
        "key": "new_agreement",
        "name": "New agreement",
        "fields": [
            {"key": "agreement_type", "label": "Kind", "kind": "select", "required": True},
            {"key": "entity_id", "label": "Entity", "kind": "select", "required": True},
            {"key": "counterparty_id", "label": "Counterparty", "kind": "select"},
        ],
    },
    {
        "key": "amendment",
        "name": "Amendment",
        "fields": [{"key": "parent_contract_id", "label": "Contract", "kind": "text"}],
    },
]


async def err(ctx, name, args=None) -> ToolError:
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, name, args or {})
    return info.value


# ---- list_request_forms -----------------------------------------------------------------


async def test_forms_embed_register_options_and_parent_contract_note(ctx, platform):
    platform.on("GET", "/intake/forms", json=copy.deepcopy(FORMS))
    platform.on(
        "GET",
        "/legal-entities",
        json=[
            {"id": "le-1", "name": "Acme Ltd", "extra": "x"},
            {"id": "le-2", "name": "Old Co", "is_active": False},
        ],
    )
    platform.on("GET", "/counterparties", json=[{"id": "cp-1", "name": "Globex"}])

    out = await call_tool(ctx, "list_request_forms", {"counterparty_search": "glob"})

    fields = {f["key"]: f for form in out["forms"] for f in form["fields"]}
    assert fields["entity_id"]["register_options"] == [{"id": "le-1", "name": "Acme Ltd"}]
    assert fields["counterparty_id"]["register_options"] == [{"id": "cp-1", "name": "Globex"}]
    assert "register_options" not in fields["agreement_type"]
    assert "Ask your user" in fields["parent_contract_id"]["lookup_note"]
    sent = platform.calls_to("GET", "/counterparties")[0].url.params
    assert sent["q"] == "glob"
    assert sent["limit"] == "50"


async def test_forms_without_register_fields_make_no_register_calls(ctx, platform):
    platform.on("GET", "/intake/forms", json=[FORMS[1]])
    await call_tool(ctx, "list_request_forms", {})
    assert [c.url.path for c in platform.calls] == ["/api/v1/intake/forms"]


async def test_forms_filter_by_key_and_unknown_key_lists_valid_ones(ctx, platform):
    platform.on("GET", "/intake/forms", json=copy.deepcopy(FORMS))
    out = await call_tool(ctx, "list_request_forms", {"form_key": "amendment"})
    assert [f["key"] for f in out["forms"]] == ["amendment"]

    e = await err(ctx, "list_request_forms", {"form_key": "nope"})
    assert e.code == "invalid_input"
    assert "new_agreement" in e.message
    assert "amendment" in e.message


async def test_forms_degrade_when_identity_cannot_read_register(ctx, platform):
    """A create-only identity may be refused the register; the forms must still come back."""
    platform.on("GET", "/intake/forms", json=copy.deepcopy(FORMS))
    platform.on("GET", "/legal-entities", status=403, json={"detail": "no"})
    platform.on("GET", "/counterparties", json=[])
    out = await call_tool(ctx, "list_request_forms", {})
    fields = {f["key"]: f for f in out["forms"][0]["fields"]}
    assert "register_options" not in fields["entity_id"]
    assert "Ask your user" in fields["entity_id"]["lookup_note"]
    assert fields["counterparty_id"]["register_options"] == []


# ---- get_intake_request / list_my_requests ---------------------------------------------


async def test_pending_triage_has_poll_hint(ctx, platform, config):
    platform.on("GET", "/intake/requests/id-1", json=req())
    out = (await call_tool(ctx, "get_intake_request", {"request_id": "id-1"}))["request"]
    assert out["triage"] == {"state": "pending"}
    assert out["poll_after_seconds"] == config.triage_poll_seconds == 30
    assert out["next_step"] == "Aegis is triaging this request. Check again in about 30 seconds."
    assert out["description"] == "Mutual NDA for pilot"
    assert out["form"] == "new_agreement"
    assert out["owner"] is None


async def test_missing_ai_triage_is_pending_not_a_guess(ctx, platform):
    platform.on("GET", "/intake/requests/id-1", json=req(ai_triage=None))
    out = (await call_tool(ctx, "get_intake_request", {"request_id": "id-1"}))["request"]
    assert out["triage"] == {"state": "pending"}


async def test_complete_triage_has_category_workflow_and_no_poll_hint(ctx, platform):
    platform.on("GET", "/intake/requests/id-1", json=req(ai_triage=COMPLETE))
    out = (await call_tool(ctx, "get_intake_request", {"request_id": "id-1"}))["request"]
    assert out["triage"] == {
        "state": "complete",
        "category": "NDA",
        "needs_info": False,
        "workflow": "NDA fast lane",
    }
    assert out["poll_after_seconds"] is None
    assert out["next_step"] == "Waiting in the Legal intake queue for an owner."


async def test_triage_without_workflow_suggestion_is_null_workflow(ctx, platform):
    triage = {"status": "complete", "category": "MSA", "needs_info": True}
    platform.on("GET", "/intake/requests/id-1", json=req(ai_triage=triage))
    out = (await call_tool(ctx, "get_intake_request", {"request_id": "id-1"}))["request"]
    assert out["triage"]["workflow"] is None
    assert out["next_step"] == "Legal needs more information; your user will be contacted."


@pytest.mark.parametrize(
    ("over", "expected"),
    [
        ({"status": "closed"}, "This request is closed. Nothing further is needed."),
        ({"status": "approved"}, "This request is approved. Nothing further is needed."),
        (
            {"workflow": [{"label": "Legal review", "stage": "review", "active": True}]},
            "In workflow step 'Legal review' (review).",
        ),
        (
            {"assigned_to_label": "Priya Shah"},
            "Assigned to Priya Shah. Legal will choose and start the workflow.",
        ),
    ],
)
async def test_next_step_templates(ctx, platform, over, expected):
    platform.on("GET", "/intake/requests/id-1", json=req(ai_triage=COMPLETE, **over))
    out = (await call_tool(ctx, "get_intake_request", {"request_id": "id-1"}))["request"]
    assert out["next_step"] == expected


async def test_list_my_requests_keeps_order_and_limits(ctx, platform):
    rows = [req(id=f"id-{i}", ref=f"REQ-{i}") for i in (3, 2, 1)]
    platform.on("GET", "/intake/requests/mine", json=rows)
    out = await call_tool(ctx, "list_my_requests", {"limit": 2})
    assert [r["ref"] for r in out["requests"]] == ["REQ-3", "REQ-2"]
    assert (out["total"], out["returned"]) == (3, 2)
    assert "description" not in out["requests"][0]  # compact summaries


async def test_list_my_requests_default_limit_and_bad_limit(ctx, platform):
    platform.on("GET", "/intake/requests/mine", json=[req()])
    assert (await call_tool(ctx, "list_my_requests", {}))["returned"] == 1
    n = len(platform.calls)
    e = await err(ctx, "list_my_requests", {"limit": 0})
    assert e.code == "invalid_input"
    assert len(platform.calls) == n


async def test_unknown_input_field_is_named_and_makes_no_call(ctx, platform):
    e = await err(ctx, "get_intake_request", {"request_id": "id-1", "requestId": "x"})
    assert e.code == "invalid_input"
    assert "requestId" in e.message
    assert platform.calls == []


# ---- SLA / documents / REQ refs ---------------------------------------------------------

SLA = {
    "legs": [
        {
            "holder": "queue",
            "holder_user_id": None,
            "start_ts": 1761040800000.0,
            "end_ts": None,
            "elapsed_ms": 3600000.0,
            "pct_of_sla": 4,
            "breached_during_leg": False,
            "active": True,
            "unknown_future_key": {"kept": True},
        }
    ],
    "sla_ms": 86400000.0,
    "breach_ts": 1761300000000.0,
    "total_elapsed_ms": 3600000.0,
    "breached": False,
    "closed": False,
    "paused": False,
}


async def test_sla_is_passed_through_verbatim(ctx, platform):
    platform.on("GET", "/intake/requests/id-1/sla", json=SLA)
    out = await call_tool(ctx, "get_request_sla", {"request_id": "id-1"})
    assert out == {"request_id": "id-1", "sla": SLA}


DOCS = [
    {
        "id": "d-1",
        "filename": "nda.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 20480,
        "extracted_chars": 12000,
        "extracted_text": "x" * 12000,
        "extraction_quality": 0.93,
        "created_at": "2026-10-21T10:00:01+00:00",
    }
]


async def test_documents_omit_text_by_default(ctx, platform):
    platform.on("GET", "/intake/requests/id-1/documents", json=copy.deepcopy(DOCS))
    out = await call_tool(ctx, "get_request_documents", {"request_id": "id-1"})
    doc = out["documents"][0]
    assert "extracted_text" not in doc
    assert doc["filename"] == "nda.pdf"
    assert doc["extracted_chars"] == 12000
    assert doc["extraction_quality"] == 0.93


async def test_documents_include_text_is_opt_in_and_capped(ctx, platform):
    platform.on("GET", "/intake/requests/id-1/documents", json=copy.deepcopy(DOCS))
    out = await call_tool(
        ctx, "get_request_documents", {"request_id": "id-1", "include_text": True}
    )
    assert len(out["documents"][0]["extracted_text"]) == 8000


async def test_req_ref_resolves_to_id_for_every_tool_that_takes_one(ctx, platform):
    platform.on(
        "GET",
        "/intake/requests/mine",
        json=[req(id="id-9", ref="REQ-1042"), req(id="id-8", ref="REQ-10420")],
    )
    platform.on("GET", "/intake/requests/id-9", json=req(id="id-9"))
    platform.on("GET", "/intake/requests/id-9/sla", json=SLA)
    platform.on("GET", "/intake/requests/id-9/documents", json=[])

    got = await call_tool(ctx, "get_intake_request", {"request_id": "REQ-1042"})
    assert got["request"]["id"] == "id-9"
    sla = await call_tool(ctx, "get_request_sla", {"request_id": "REQ-1042"})
    assert sla["request_id"] == "id-9"
    docs = await call_tool(ctx, "get_request_documents", {"request_id": "REQ-1042"})
    assert docs["request_id"] == "id-9"
    assert platform.calls_to("GET", "/intake/requests/id-8") == []


async def test_plain_id_does_not_look_up_mine(ctx, platform):
    platform.on("GET", "/intake/requests/abc-123", json=req(id="abc-123"))
    await call_tool(ctx, "get_intake_request", {"request_id": "abc-123"})
    assert platform.calls_to("GET", "/intake/requests/mine") == []


async def test_unknown_ref_is_not_found_and_stops_before_the_request_call(ctx, platform):
    platform.on("GET", "/intake/requests/mine", json=[req(ref="REQ-1")])
    e = await err(ctx, "get_request_sla", {"request_id": "REQ-999"})
    assert e.code == "not_found"
    assert "No request with that id is visible" in e.message
    assert len(platform.calls) == 1


# ---- error mapping on read paths --------------------------------------------------------

READS = [
    ("get_intake_request", {"request_id": "id-1"}, "/intake/requests/id-1"),
    ("get_request_sla", {"request_id": "id-1"}, "/intake/requests/id-1/sla"),
    ("get_request_documents", {"request_id": "id-1"}, "/intake/requests/id-1/documents"),
    ("list_my_requests", {}, "/intake/requests/mine"),
    ("list_request_forms", {}, "/intake/forms"),
]


@pytest.mark.parametrize(("tool", "args", "path"), READS)
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (403, "permission_denied"),
        (404, "not_found"),
        (422, "invalid_input"),
        (500, "service_unavailable"),
        (503, "service_unavailable"),
    ],
)
async def test_read_errors_are_mapped(ctx, platform, tool, args, path, status, code):
    platform.on("GET", path, status=status, json={"detail": "because", "request_id": "rq-7"})
    e = await err(ctx, tool, args)
    assert e.code == code
    if status != 503:  # a retried 503 is an outage; the client keeps no response for it
        assert e.platform_request_id == "rq-7"
    assert e.to_payload()["error"]["code"] == code


async def test_403_names_the_platforms_reason(ctx, platform):
    platform.on("GET", "/intake/requests/id-1", status=403, json={"detail": "Missing intake"})
    e = await err(ctx, "get_intake_request", {"request_id": "id-1"})
    assert e.message == (
        "Aegis refused: Missing intake. This agent's identity is not allowed to do that."
    )


async def test_404_never_says_whether_the_request_exists(ctx, platform):
    platform.on("GET", "/intake/requests/id-1", status=404, json={"detail": "belongs to bob"})
    e = await err(ctx, "get_intake_request", {"request_id": "id-1"})
    assert e.message == "No request with that id is visible to this agent."


async def test_422_list_detail_is_flattened(ctx, platform):
    detail = [{"loc": ["query", "limit"], "msg": "bad"}, {"loc": ["body", "a", "b"], "msg": "no"}]
    platform.on("GET", "/intake/requests/mine", status=422, json={"detail": detail})
    e = await err(ctx, "list_my_requests")
    assert e.message == "limit: bad; a.b: no"


async def test_429_uses_body_seconds_then_header_then_default(ctx, platform):
    path = "/intake/requests/mine"
    platform.on("GET", path, status=429, json={"detail": "slow", "retry_after_seconds": 42})
    e = await err(ctx, "list_my_requests")
    assert (e.code, e.retry_after_seconds) == ("rate_limited", 42)
    assert e.message == "Too many requests to Aegis. Try again in 42 seconds. Nothing was read."

    platform.on("GET", path, httpx.Response(429, headers={"Retry-After": "17"}, json={}))
    assert (await err(ctx, "list_my_requests")).retry_after_seconds == 17

    platform.on("GET", path, httpx.Response(429, json={}))
    assert (await err(ctx, "list_my_requests")).retry_after_seconds == 60


@pytest.mark.parametrize(
    "exc", [httpx.ConnectTimeout("t"), httpx.ReadTimeout("t"), httpx.ConnectError("c")]
)
async def test_timeouts_and_connect_errors_are_service_unavailable_after_retry(
    ctx, platform, exc
):
    platform.on("GET", "/intake/requests/id-1", exc)
    e = await err(ctx, "get_intake_request", {"request_id": "id-1"})
    assert e.code == "service_unavailable"
    assert "Nothing was done" in e.message
    assert len(platform.calls) == 2  # one retry, then the error


async def test_read_retries_once_and_then_succeeds(ctx, platform):
    platform.on("GET", "/intake/requests/id-1", httpx.ReadTimeout("t"), httpx.Response(
        200, json=req()
    ))
    out = await call_tool(ctx, "get_intake_request", {"request_id": "id-1"})
    assert out["request"]["ref"] == "REQ-1042"
    assert len(platform.calls) == 2


def test_fake_platform_type_is_used():
    assert FakePlatform


async def test_register_options_skip_inactive_by_platform_field_name(ctx, platform):
    """The platform marks rows with `active`; `is_active` is tolerated too."""
    platform.on("GET", "/intake/forms", json=[FORMS[0]])
    platform.on(
        "GET",
        "/legal-entities",
        json=[
            {"id": "le-1", "name": "Live", "active": True},
            {"id": "le-2", "name": "Dormant", "active": False},
            {"id": "le-3", "name": "Old", "is_active": False},
        ],
    )
    platform.on("GET", "/counterparties", json=[])
    out = await call_tool(ctx, "list_request_forms", {})
    entity = next(f for f in out["forms"][0]["fields"] if f["key"] == "entity_id")
    assert entity["register_options"] == [{"id": "le-1", "name": "Live"}]
