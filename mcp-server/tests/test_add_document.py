"""add_document. Failure modes guarded: an upload retried after a lost answer (it is not
idempotent, so a second attempt could attach twice), an oversize file reaching the platform,
and a failure reported as "added"."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from conftest import KEY, FakePlatform

from aegis_intake_mcp.errors import ToolError
from aegis_intake_mcp.limits import MAX_FILE_BYTES
from aegis_intake_mcp.tools import ToolContext, call_tool

DOC = {"id": "d1", "filename": "x.pdf", "size_bytes": 5, "extracted_text": "secret text"}
DOCS_PATH = "/intake/requests/req-1/documents"


def args(**over):
    base = {
        "request_id": "req-1",
        "filename": "x.pdf",
        "mime_type": "application/pdf",
        "content_base64": base64.b64encode(b"hello").decode(),
    }
    base.update(over)
    return base


@pytest.fixture
def ctx(client, config) -> ToolContext:
    return ToolContext(client=client, config=config, headers={"authorization": KEY})


def docs_calls(platform: FakePlatform):
    return platform.calls_to("POST", DOCS_PATH)


async def test_success_maps_body_and_drops_extracted_text(ctx, platform):
    platform.on("POST", DOCS_PATH, status=201, json=DOC)
    out = await call_tool(ctx, "add_document", args())
    assert out["outcome"] == "added"
    assert out["message"] == "Added 'x.pdf' to the request."
    assert "extracted_text" not in out["document"]
    (call,) = docs_calls(platform)
    assert json.loads(call.content) == {
        "filename": "x.pdf",
        "mime_type": "application/pdf",
        "content_b64": base64.b64encode(b"hello").decode(),
    }


async def test_oversize_is_refused_before_any_upstream_call(ctx, platform):
    big = base64.b64encode(b"\0" * (MAX_FILE_BYTES + 1)).decode()
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, "add_document", args(content_base64=big))
    assert info.value.code == "too_large"
    assert platform.calls == []


async def test_url_is_refused_and_nothing_is_fetched(ctx, platform):
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, "add_document", args(content_base64="https://evil.example/x"))
    assert info.value.code == "invalid_input"
    assert platform.calls == []


async def test_upstream_415_is_unsupported_file_type(ctx, platform):
    platform.on(
        "POST",
        DOCS_PATH,
        status=415,
        json={"detail": "x.exe is not accepted. Use PDF, Word, plain text, PNG or JPEG."},
    )
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, "add_document", args())
    assert info.value.code == "unsupported_file_type"
    assert "x.exe" in info.value.message


async def test_timeout_is_outcome_unknown_after_exactly_one_attempt(ctx, platform):
    platform.on("POST", DOCS_PATH, httpx.ReadTimeout("slow"))
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, "add_document", args())
    assert info.value.code == "outcome_unknown"
    assert "get_request_documents" in info.value.message
    assert len(docs_calls(platform)) == 1


async def test_503_is_not_retried_either(ctx, platform):
    platform.on("POST", DOCS_PATH, httpx.Response(503))
    with pytest.raises(ToolError) as info:
        await call_tool(ctx, "add_document", args())
    assert info.value.code == "outcome_unknown"
    assert len(docs_calls(platform)) == 1


async def test_req_reference_is_resolved_then_uploaded(ctx, platform):
    platform.on("GET", "/intake/requests/mine", json=[{"id": "req-1", "ref": "REQ-7"}])
    platform.on("POST", DOCS_PATH, status=201, json=DOC)
    out = await call_tool(ctx, "add_document", args(request_id="REQ-7"))
    assert out["outcome"] == "added"
