"""Write-tool error mapping. Failure mode guarded: any platform failure surfacing as a
success-looking result, or an error that hides what the agent must do next."""

from __future__ import annotations

import base64

import httpx
import pytest
from conftest import KEY

from aegis_intake_mcp.errors import ToolError
from aegis_intake_mcp.tools import ToolContext, call_tool

FORMS = [{"key": "nda_only", "name": "NDA", "fields": []}]
B64 = base64.b64encode(b"hello").decode()

CREATE = (
    "create_intake_request",
    {"form_key": "nda_only", "answers": {}},
    "POST",
    "/intake/requests",
)
ADD = (
    "add_document",
    {"request_id": "req-1", "filename": "a.txt", "mime_type": "text/plain", "content_base64": B64},
    "POST",
    "/intake/requests/req-1/documents",
)


@pytest.fixture
def ctx(client, config) -> ToolContext:
    return ToolContext(client=client, config=config, headers={"authorization": KEY})


async def run(ctx, platform, tool, reply) -> ToolError:
    name, args, method, path = tool
    platform.on("GET", "/intake/forms", json=FORMS)
    platform.on(method, path, reply)
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, name, args)
    return info.value


@pytest.mark.parametrize("tool", [CREATE, ADD], ids=["create", "add"])
@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, {"detail": "bad"}, "authentication_failed"),
        (403, {"detail": "Missing permission"}, "permission_denied"),
        (404, {"detail": "nope"}, "not_found"),
        (409, {"detail": "Key reused"}, "conflict"),
        (413, {"detail": "Too big"}, "too_large"),
        (415, {"detail": "Bad type"}, "unsupported_file_type"),
        (
            422,
            {"detail": [{"loc": ["body", "field_values", "x"], "msg": "required"}]},
            "invalid_input",
        ),
    ],
)
async def test_status_mapping(ctx, platform, tool, status, body, code):
    err = await run(ctx, platform, tool, httpx.Response(status, json=body))
    assert err.code == code
    assert err.to_payload()["error"]["code"] == code  # an error result, never data


async def test_422_list_detail_is_flattened(ctx, platform):
    reply = httpx.Response(422, json={"detail": [{"loc": ["body", "priority"], "msg": "bad"}]})
    err = await run(ctx, platform, CREATE, reply)
    assert err.message == "priority: bad"


@pytest.mark.parametrize("tool", [CREATE, ADD], ids=["create", "add"])
async def test_429_uses_body_over_header(ctx, platform, tool):
    reply = httpx.Response(
        429, json={"detail": "slow", "retry_after_seconds": 42}, headers={"Retry-After": "9"}
    )
    err = await run(ctx, platform, tool, reply)
    assert err.code == "rate_limited" and err.retry_after_seconds == 42
    assert "42 seconds" in err.message and "Nothing was" in err.message


async def test_429_falls_back_to_header(ctx, platform):
    reply = httpx.Response(429, json={}, headers={"Retry-After": "7"})
    err = await run(ctx, platform, CREATE, reply)
    assert err.retry_after_seconds == 7 and "Nothing was filed" in err.message


@pytest.mark.parametrize("exc", [httpx.ConnectError("down"), httpx.ReadTimeout("slow")])
@pytest.mark.parametrize("tool", [CREATE, ADD], ids=["create", "add"])
async def test_lost_answer_is_outcome_unknown_not_success(ctx, platform, tool, exc):
    err = await run(ctx, platform, tool, exc)
    assert err.code == "outcome_unknown"
    if tool is CREATE:
        assert err.idempotency_key and err.idempotency_key in err.message


async def test_503_after_retries_is_outcome_unknown_for_writes(ctx, platform):
    err = await run(ctx, platform, CREATE, httpx.Response(503))
    assert err.code == "outcome_unknown" and err.idempotency_key


@pytest.mark.parametrize("status", [500, 501])
async def test_write_5xx_is_outcome_unknown_never_nothing_happened(ctx, platform, status):
    """A write can fail after its commit; telling the agent 'nothing was done' invites a
    duplicate legal request."""
    err = await run(ctx, platform, ADD, httpx.Response(status, json={"detail": "boom"}))
    assert err.code == "outcome_unknown"
    assert "Nothing was done" not in err.message
    assert "get_request_documents" in err.message

    err = await run(ctx, platform, CREATE, httpx.Response(status, json={"detail": "boom"}))
    assert err.code == "outcome_unknown"
    assert err.idempotency_key
    assert f"idempotency_key '{err.idempotency_key}'" in err.message


async def test_write_5xx_keeps_the_callers_own_key(ctx, platform):
    name, args, method, path = CREATE
    platform.on("GET", "/intake/forms", json=FORMS)
    platform.on(method, path, httpx.Response(500, json={}))
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, name, {**args, "idempotency_key": "agent-key-0001"})
    assert info.value.idempotency_key == "agent-key-0001"


async def test_read_500_stays_service_unavailable(ctx, platform):
    platform.on("GET", "/intake/requests/mine", status=500, json={})
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, "list_my_requests", {})
    assert info.value.code == "service_unavailable"
