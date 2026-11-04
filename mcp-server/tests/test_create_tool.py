"""create_intake_request. Failure modes guarded: a retry that files twice (key or body
changing between attempts), a repeat reported as a fresh filing, a lost answer reported as
success or failure instead of outcome_unknown, a missing form filing anyway."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from conftest import KEY, FakePlatform

from aegis_intake_mcp.errors import ToolError
from aegis_intake_mcp.tools import ToolContext, call_tool

FORMS = [
    {"key": "new_agreement", "name": "New agreement", "fields": []},
    {"key": "nda_only", "name": "NDA", "fields": []},
]
CREATED = {
    "id": "id-1",
    "ref": "REQ-1042",
    "subject": "Acme NDA",
    "description": "d",
    "type_label": "New agreement · NDA",
    "field_values": {"request_form": "new_agreement"},
    "status": "open",
    "stage": "new",
    "ai_triage": {"status": "pending"},
    "workflow": [],
}


@pytest.fixture
def ctx(client, config) -> ToolContext:
    return ToolContext(client=client, config=config, headers={"authorization": KEY})


def setup(platform: FakePlatform, *replies) -> None:
    platform.on("GET", "/intake/forms", json=FORMS)
    platform.on("POST", "/intake/requests", *replies)


def ok(body=None, status=201) -> httpx.Response:
    return httpx.Response(status, json=body or CREATED)


def posted(platform: FakePlatform) -> list[dict]:
    return [json.loads(c.content) for c in platform.calls_to("POST", "/intake/requests")]


async def test_body_mapping(ctx, platform):
    setup(platform, ok())
    answers = {"agreement_type": "NDA", "department": "Sales", "purpose": "Pilot with Acme"}
    out = await call_tool(
        ctx, "create_intake_request", {"form_key": "new_agreement", "answers": answers}
    )
    (body,) = posted(platform)
    assert body["type_label"] == "New agreement · NDA"
    assert body["department"] == "Sales"
    assert body["priority"] == "Medium"
    assert body["description"] == "Pilot with Acme"
    assert body["field_values"] == {**answers, "request_form": "new_agreement"}
    assert body["source"] == "api"
    assert body["attachments"] == []
    assert out["outcome"] == "created"
    assert out["message"] == "Filed as REQ-1042."
    assert out["request"]["ref"] == "REQ-1042"
    assert out["idempotency_key"] == body["idempotency_key"]


async def test_other_form_label_and_description_fallbacks(ctx, platform):
    setup(platform, ok())
    await call_tool(ctx, "create_intake_request", {"form_key": "nda_only", "answers": {}})
    (body,) = posted(platform)
    assert body["type_label"] == "NDA Request"
    assert body["description"] == "NDA"  # no input, no answers: the form name
    assert body["department"] is None


async def test_priority_high_within_seven_days(ctx, platform):
    setup(platform, ok())
    soon = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()
    later = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
    await call_tool(
        ctx, "create_intake_request", {"form_key": "nda_only", "answers": {"needed_by": soon}}
    )
    await call_tool(
        ctx, "create_intake_request", {"form_key": "nda_only", "answers": {"needed_by": later}}
    )
    first, second = posted(platform)
    assert (first["priority"], second["priority"]) == ("High", "Medium")


async def test_generated_key_when_absent_and_given_key_kept(ctx, platform):
    setup(platform, ok())
    a = await call_tool(ctx, "create_intake_request", {"form_key": "nda_only", "answers": {}})
    assert a["idempotency_key"].startswith("mcp-") and len(a["idempotency_key"]) >= 8
    b = await call_tool(
        ctx,
        "create_intake_request",
        {"form_key": "nda_only", "answers": {}, "idempotency_key": "my-key-12345"},
    )
    assert b["idempotency_key"] == "my-key-12345"
    assert posted(platform)[1]["idempotency_key"] == "my-key-12345"


async def test_attachment_mapped_to_platform_names(ctx, platform):
    setup(platform, ok())
    b64 = base64.b64encode(b"hello").decode()
    att = {"filename": "a.txt", "mime_type": "text/plain", "content_base64": b64}
    await call_tool(
        ctx,
        "create_intake_request",
        {"form_key": "nda_only", "answers": {}, "attachments": [att]},
    )
    (body,) = posted(platform)
    assert body["attachments"] == [
        {"filename": "a.txt", "mime_type": "text/plain", "content_b64": b64}
    ]


async def test_bad_attachment_makes_no_upstream_call(ctx, platform):
    setup(platform, ok())
    att = {"filename": "a.txt", "mime_type": "text/plain", "content_base64": "https://x.example/a"}
    with pytest.raises(ToolError) as info:
        await call_tool(
            ctx,
            "create_intake_request",
            {"form_key": "nda_only", "answers": {}, "attachments": [att]},
        )
    assert info.value.code == "invalid_input"
    assert platform.calls == []


async def test_repeat_is_reported_as_already_filed(ctx, platform):
    setup(platform, ok({**CREATED, "idempotent_repeat": True}, status=200))
    out = await call_tool(
        ctx,
        "create_intake_request",
        {"form_key": "nda_only", "answers": {}, "idempotency_key": "my-key-12345"},
    )
    assert out["outcome"] == "already_filed"
    assert "REQ-1042" in out["message"] and "no new request was created" in out["message"]


async def test_timeout_then_success_retries_with_identical_key_and_body(ctx, platform):
    setup(platform, httpx.ReadTimeout("slow"), ok())
    out = await call_tool(ctx, "create_intake_request", {"form_key": "nda_only", "answers": {}})
    first, second = platform.calls_to("POST", "/intake/requests")
    assert first.content == second.content
    assert out["outcome"] == "created"
    assert out["idempotency_key"] == json.loads(first.content)["idempotency_key"]


async def test_double_timeout_is_outcome_unknown_with_the_key(ctx, platform):
    setup(platform, httpx.ReadTimeout("slow"))
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, "create_intake_request", {"form_key": "nda_only", "answers": {}})
    err = info.value
    sent = posted(platform)
    assert len(sent) == 2 and sent[0] == sent[1]
    assert err.code == "outcome_unknown"
    assert err.idempotency_key == sent[0]["idempotency_key"]
    assert err.idempotency_key in err.message


@pytest.mark.parametrize("args", [{"answers": {}}, {"form_key": "", "answers": {}}])
async def test_missing_form_key_makes_no_upstream_call(ctx, platform, args):
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, "create_intake_request", args)
    assert info.value.code == "invalid_input"
    assert platform.calls == []


async def test_unknown_form_key_is_invalid_input_and_files_nothing(ctx, platform):
    setup(platform, ok())
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, "create_intake_request", {"form_key": "bogus", "answers": {}})
    assert info.value.code == "invalid_input" and "new_agreement" in info.value.message
    assert platform.calls_to("POST", "/intake/requests") == []


async def test_409_is_conflict(ctx, platform):
    setup(platform, httpx.Response(409, json={"detail": "That idempotency key was used"}))
    with pytest.raises(ToolError) as info:
        await call_tool(
            ctx,
            "create_intake_request",
            {"form_key": "nda_only", "answers": {}, "idempotency_key": "my-key-12345"},
        )
    assert info.value.code == "conflict"
    assert len(posted(platform)) == 1  # a 409 is never retried


async def test_500_then_retry_with_same_key_reports_already_filed(ctx, platform):
    """The M2 scenario: the platform committed, then 500'd. The error hands back the key and
    reusing it returns the original instead of filing twice."""
    setup(platform, httpx.Response(500, json={"detail": "boom"}))
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, "create_intake_request", {"form_key": "nda_only", "answers": {}})
    err = info.value
    assert err.code == "outcome_unknown"
    key = err.idempotency_key
    assert key
    assert key in err.message

    setup(platform, ok({**CREATED, "idempotent_repeat": True}, status=200))
    out = await call_tool(
        ctx,
        "create_intake_request",
        {"form_key": "nda_only", "answers": {}, "idempotency_key": key},
    )
    assert out["outcome"] == "already_filed"
    assert out["idempotency_key"] == key
    assert posted(platform)[-1]["idempotency_key"] == key


@pytest.mark.parametrize(
    ("form_name", "label"),
    [
        ("Cancel a request", "Cancel a request"),
        ("cancel a REQUEST", "cancel a REQUEST"),
        ("Statement of Work", "Statement of Work Request"),
    ],
)
async def test_type_label_fallback_does_not_double_request(ctx, platform, form_name, label):
    forms = [{"key": "other_form", "name": form_name, "fields": []}]
    platform.on("GET", "/intake/forms", json=forms)
    platform.on("POST", "/intake/requests", ok())
    await call_tool(ctx, "create_intake_request", {"form_key": "other_form", "answers": {}})
    assert posted(platform)[0]["type_label"] == label
