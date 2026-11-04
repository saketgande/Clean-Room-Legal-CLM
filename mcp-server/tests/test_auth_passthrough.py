"""Credential pass-through. Failure modes guarded: a call going upstream without a valid key
(FR-12), one agent's key reaching another agent's call (FR-13), a revoked key still working
because something cached it, and the key kept anywhere but the single call that used it."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from conftest import KEY, FakePlatform

from aegis_intake_mcp.aegis_client import AegisClient
from aegis_intake_mcp.errors import ToolError
from aegis_intake_mcp.tools import TOOLS, ToolContext, call_tool

KEY_A = "Bearer clm_" + "A" * 24
KEY_B = "Bearer clm_" + "B" * 24

TOOL_ARGS = {
    "list_request_forms": {},
    "get_intake_request": {"request_id": "id-1"},
    "list_my_requests": {},
    "get_request_sla": {"request_id": "id-1"},
    "get_request_documents": {"request_id": "id-1"},
}


def ctx_for(client, config, header: str | None, name: str = "authorization") -> ToolContext:
    headers = {} if header is None else {name: header}
    return ToolContext(client=client, config=config, headers=headers)


def seed(platform: FakePlatform) -> None:
    platform.on("GET", "/intake/forms", json=[])
    platform.on("GET", "/intake/requests/mine", json=[{"id": "id-1", "ref": "REQ-1"}])
    platform.on("GET", "/intake/requests/id-1", json={"id": "id-1", "ai_triage": None})
    platform.on("GET", "/intake/requests/id-1/sla", json={"legs": []})
    platform.on("GET", "/intake/requests/id-1/documents", json=[])


def test_every_registered_tool_is_covered_here():
    assert set(TOOL_ARGS) <= set(TOOLS)


@pytest.mark.parametrize("tool", sorted(TOOL_ARGS))
async def test_exact_header_is_forwarded_on_every_upstream_call(client, config, platform, tool):
    seed(platform)
    args = dict(TOOL_ARGS[tool])
    if "request_id" in args:
        args["request_id"] = "REQ-1"  # forces the extra lookup call as well
    await call_tool(ctx_for(client, config, KEY_A), tool, args)
    assert platform.calls
    assert {c.headers["authorization"] for c in platform.calls} == {KEY_A}


async def test_forms_register_calls_carry_the_same_header(client, config, platform):
    platform.on(
        "GET",
        "/intake/forms",
        json=[{"key": "f", "fields": [{"key": "entity_id"}, {"key": "counterparty_id"}]}],
    )
    platform.on("GET", "/legal-entities", json=[])
    platform.on("GET", "/counterparties", json=[])
    await call_tool(ctx_for(client, config, KEY_A), "list_request_forms", {})
    assert len(platform.calls) == 3
    assert {c.headers["authorization"] for c in platform.calls} == {KEY_A}


async def test_header_name_is_case_insensitive(client, config, platform):
    seed(platform)
    await call_tool(ctx_for(client, config, KEY_A, "Authorization"), "list_my_requests", {})
    assert platform.calls[0].headers["authorization"] == KEY_A


async def test_two_concurrent_calls_with_different_keys_never_cross(config):
    """Each reply echoes the key its request carried; a shared or cached header would mix
    the answers up."""
    saw: list[str] = []

    async def echo(request: httpx.Request) -> httpx.Response:
        key = request.headers["authorization"]
        saw.append(key)
        await asyncio.sleep(0)  # yield so the calls interleave
        return httpx.Response(200, json=[{"id": "id-1", "ref": "REQ-1", "subject": key[-6:]}])

    c = AegisClient(config, transport=httpx.MockTransport(echo), retry_backoff_seconds=0)
    try:
        results = await asyncio.gather(
            *[call_tool(ctx_for(c, config, k), "list_my_requests", {}) for k in (KEY_A, KEY_B) * 5]
        )
    finally:
        await c.aclose()
    assert [r["requests"][0]["subject"] for r in results] == [KEY_A[-6:], KEY_B[-6:]] * 5
    assert sorted(saw) == sorted([KEY_A, KEY_B] * 5)


async def test_client_holds_no_default_authorization(client):
    assert "authorization" not in client._http.headers


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "clm_" + "a" * 32,  # no Bearer scheme
        "Bearer " + "a" * 32,  # not a clm_ key
        "Bearer sk-ant-" + "a" * 32,
        "Bearer clm_short",  # under 16 chars
        "Bearer clm_" + "a" * 300,  # over 256
        "Bearer clm_" + "a" * 32 + " extra",
        "Bearer clm_" + "a" * 32 + "\n",
        "Bearer clm_" + "a" * 16 + "!" * 4,
        "Basic dXNlcjpwYXNz",
    ],
)
@pytest.mark.parametrize("tool", sorted(TOOL_ARGS))
async def test_missing_or_malformed_key_is_refused_with_zero_upstream_calls(
    client, config, platform, tool, header
):
    seed(platform)
    with pytest.raises(ToolError) as info:
        await call_tool(ctx_for(client, config, header), tool, TOOL_ARGS[tool])
    assert info.value.code == "authentication_failed"
    assert "Nothing was done" in info.value.message
    assert platform.calls == []


async def test_no_authorization_header_at_all(client, config, platform):
    seed(platform)
    ctx = ToolContext(client=client, config=config, headers={"x-other": "1"})
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, "list_my_requests", {})
    assert info.value.code == "authentication_failed"
    assert platform.calls == []


async def test_upstream_401_is_authentication_failed_and_nothing_is_cached(
    client, config, platform
):
    """Revocation mid-session: the same key works, is then revoked, and the very next call
    fails. The tool server remembers nothing between calls (FR-41)."""
    ok = httpx.Response(200, json=[])
    revoked = httpx.Response(401, json={"detail": "Invalid or revoked API key"})
    platform.on("GET", "/intake/requests/mine", ok, revoked)
    ctx = ctx_for(client, config, KEY_A)

    assert (await call_tool(ctx, "list_my_requests", {}))["total"] == 0
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, "list_my_requests", {})
    assert info.value.code == "authentication_failed"
    assert "revoked" in info.value.message
    assert len(platform.calls) == 2  # a 401 is not retried


async def test_401_during_ref_resolution_stops_the_tool(client, config, platform):
    platform.on("GET", "/intake/requests/mine", status=401, json={"detail": "no"})
    with pytest.raises(ToolError) as info:
        await call_tool(ctx_for(client, config, KEY_A), "get_request_sla", {"request_id": "REQ-1"})
    assert info.value.code == "authentication_failed"
    assert len(platform.calls) == 1


async def test_default_test_key_constant_matches_the_pattern(client, config, platform):
    seed(platform)
    await call_tool(ctx_for(client, config, KEY), "list_my_requests", {})
    assert platform.calls[0].headers["authorization"] == KEY


@pytest.mark.parametrize("scheme", ["bearer", "BEARER", "BeArEr"])
async def test_bearer_scheme_is_case_insensitive_and_forwarded_verbatim(
    client, config, platform, scheme
):
    """RFC 7235: the scheme is case-insensitive. What goes upstream is what the caller sent."""
    seed(platform)
    header = f"{scheme} clm_" + "c" * 24
    await call_tool(ctx_for(client, config, header), "list_my_requests", {})
    assert platform.calls[0].headers["authorization"] == header


@pytest.mark.parametrize(
    "header",
    ["Basic dXNlcjpwYXNz", "clm_" + "c" * 24, "Bearer CLM_" + "c" * 24, "Token clm_" + "c" * 24],
)
async def test_other_schemes_and_uppercase_token_prefix_are_refused(
    client, config, platform, header
):
    seed(platform)
    with pytest.raises(ToolError) as info:
        await call_tool(ctx_for(client, config, header), "list_my_requests", {})
    assert info.value.code == "authentication_failed"
    assert platform.calls == []
