"""The caller's key must live only for one call. Failure modes guarded: the key landing in a
log record (any logger, at DEBUG), in client default headers, or the client being steered to
another host or endpoint (AC-26, FR-2, FR-11)."""

from __future__ import annotations

import base64
import logging

import httpx
import pytest
from conftest import KEY, FakePlatform

from aegis_intake_mcp.aegis_client import ClientPolicyError
from aegis_intake_mcp.errors import ToolError
from aegis_intake_mcp.tools import TOOLS, ToolContext, call_tool

SECRET = KEY.removeprefix("Bearer ")
B64 = base64.b64encode(b"hello").decode()
REQ = {"id": "r1", "ref": "REQ-1", "status": "open", "ai_triage": {"status": "pending"}}

CALLS = {
    "list_request_forms": {},
    "create_intake_request": {"form_key": "nda_only", "answers": {}},
    "get_intake_request": {"request_id": "r1"},
    "list_my_requests": {},
    "get_request_sla": {"request_id": "r1"},
    "get_request_documents": {"request_id": "r1"},
    "add_document": {
        "request_id": "r1",
        "filename": "a.txt",
        "mime_type": "text/plain",
        "content_base64": B64,
    },
}


def fake() -> FakePlatform:
    p = FakePlatform()
    p.on("GET", "/intake/forms", json=[{"key": "nda_only", "name": "NDA", "fields": []}])
    p.on("POST", "/intake/requests", status=201, json=REQ)
    p.on("GET", "/intake/requests/mine", json=[REQ])
    p.on("GET", "/intake/requests/r1", json=REQ)
    p.on("GET", "/intake/requests/r1/sla", json={})
    p.on("GET", "/intake/requests/r1/documents", json=[])
    p.on("POST", "/intake/requests/r1/documents", status=201, json={"id": "d1"})
    return p


def test_every_tool_is_covered():
    assert set(CALLS) == set(TOOLS)


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_key_never_appears_in_logs(name, config, caplog):
    from aegis_intake_mcp.aegis_client import AegisClient

    platform = fake()
    client = AegisClient(
        config, transport=httpx.MockTransport(platform.handler), retry_backoff_seconds=0
    )
    ctx = ToolContext(client=client, config=config, headers={"Authorization": KEY})
    with caplog.at_level(logging.DEBUG):
        try:
            await call_tool(ctx, name, CALLS[name])
        except ToolError:
            pass
        # A failing call path must not leak either.
        platform.on("POST", "/intake/requests", httpx.Response(500, text=KEY))
        try:
            await call_tool(ctx, name, CALLS[name])
        except ToolError:
            pass
    await client.aclose()
    assert platform.calls  # the tool really ran
    assert all(c.headers["authorization"] == KEY for c in platform.calls)
    for record in caplog.records:
        assert SECRET not in record.getMessage()
        assert SECRET not in str(record.__dict__)


def test_client_keeps_no_default_authorization_header(client):
    assert "authorization" not in {k.lower() for k in client._http.headers}


async def test_client_rejects_absolute_urls_and_unlisted_paths(client, platform):
    for method, path in [
        ("GET", "http://evil.example/intake/forms"),
        ("GET", "https://backend:8000/api/v1/intake/forms"),
        ("GET", "/users"),
        ("DELETE", "/intake/requests/{id}"),
        ("POST", "/intake/forms"),
    ]:
        with pytest.raises(ClientPolicyError):
            await client.request(method, path, auth_header=KEY, path_params=None)
    assert platform.calls == []
