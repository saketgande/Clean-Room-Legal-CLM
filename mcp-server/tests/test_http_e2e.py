"""Over-the-wire behaviour of the real ASGI app (FR-12, FR-53..FR-56): raw JSON-RPC through
httpx's ASGI transport, with only the Aegis platform faked."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging

import httpx
from conftest import API, KEY, FakePlatform

from aegis_intake_mcp.aegis_client import AegisClient
from aegis_intake_mcp.config import Config
from aegis_intake_mcp.server import INSTRUCTIONS, RedactionFilter, create_app
from aegis_intake_mcp.tools import TOOLS

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
OTHER_KEY = "Bearer clm_" + "b" * 32


@contextlib.asynccontextmanager
async def running(transport: httpx.AsyncBaseTransport, config: Config | None = None):
    config = config or Config(api_base_url=API)
    client = AegisClient(config, transport=transport, retry_backoff_seconds=0)
    app = create_app(config, client=client)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost:8001"
            ) as http:
                yield http
    finally:
        await client.aclose()


def fake(platform: FakePlatform) -> httpx.MockTransport:
    return httpx.MockTransport(platform.handler)


def rpc(method: str, params: dict | None = None) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}


def tool_call(name: str, arguments: dict) -> dict:
    return rpc("tools/call", {"name": name, "arguments": arguments})


def error_of(response: httpx.Response) -> dict:
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    return json.loads(result["content"][0]["text"])["error"]


async def test_tools_list_works_without_a_key(platform):
    """Discovery must work before a key is configured; it is static metadata, no platform call."""
    async with running(fake(platform)) as http:
        response = await http.post("/mcp", json=rpc("tools/list"), headers=HEADERS)
    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    assert {t["name"] for t in tools} == set(TOOLS) and len(tools) == 7
    assert all(t["inputSchema"].get("additionalProperties") is False for t in tools)
    assert platform.calls == []


async def test_initialize_returns_instructions(platform):
    """The model is told up front that returned text is data, not instructions."""
    async with running(fake(platform)) as http:
        response = await http.post(
            "/mcp",
            json=rpc(
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            ),
            headers=HEADERS,
        )
    result = response.json()["result"]
    assert result["instructions"] == INSTRUCTIONS
    assert result["serverInfo"]["name"] == "aegis-intake"


async def test_call_without_a_key_fails_with_no_platform_call(platform):
    """FR-12: a missing key is a readable authentication_failed and nothing leaves the server."""
    async with running(fake(platform)) as http:
        response = await http.post("/mcp", json=tool_call("list_my_requests", {}), headers=HEADERS)
    assert error_of(response)["code"] == "authentication_failed"
    assert platform.calls == []


async def test_call_with_malformed_key_is_refused(platform):
    async with running(fake(platform)) as http:
        response = await http.post(
            "/mcp",
            json=tool_call("list_my_requests", {}),
            headers={**HEADERS, "Authorization": "Bearer not-a-key"},
        )
    assert error_of(response)["code"] == "authentication_failed"
    assert platform.calls == []


async def test_successful_call_forwards_each_callers_own_key(platform):
    """The Bearer from a request reaches the platform for that call only; a second caller is
    never served with the first one's key (stateless, FR-56)."""
    platform.on("GET", "/intake/requests/mine", json=[])
    async with running(fake(platform)) as http:
        first = await http.post(
            "/mcp",
            json=tool_call("list_my_requests", {}),
            headers={**HEADERS, "Authorization": KEY},
        )
        second = await http.post(
            "/mcp",
            json=tool_call("list_my_requests", {}),
            headers={**HEADERS, "Authorization": OTHER_KEY},
        )
    result = first.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"] == {"requests": [], "total": 0, "returned": 0}
    assert second.json()["result"]["isError"] is False
    assert [c.headers["authorization"] for c in platform.calls] == [KEY, OTHER_KEY]


async def test_unknown_argument_is_refused_naming_the_field(platform):
    """AC-30: the agent is told which input name is wrong, and nothing is sent."""
    async with running(fake(platform)) as http:
        response = await http.post(
            "/mcp",
            json=tool_call("list_my_requests", {"limit": 5, "colour": "red"}),
            headers={**HEADERS, "Authorization": KEY},
        )
    err = error_of(response)
    assert err["code"] == "invalid_input"
    assert "colour" in err["message"]
    assert platform.calls == []


async def test_unknown_tool_is_a_clear_error(platform):
    async with running(fake(platform)) as http:
        response = await http.post(
            "/mcp",
            json=tool_call("delete_everything", {}),
            headers={**HEADERS, "Authorization": KEY},
        )
    err = error_of(response)
    assert err["code"] == "invalid_input" and "delete_everything" in err["message"]


async def test_deadline_gives_a_timeout_error_that_does_not_claim_success():
    """The hard per-call cap: a platform that never answers yields outcome_unknown (with the
    key to retry with), never a made-up success."""
    config = Config(api_base_url=API, tool_deadline_seconds=0.2)

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=[])

    async with running(httpx.MockTransport(slow), config) as http:
        response = await http.post(
            "/mcp",
            json=tool_call(
                "create_intake_request",
                {"form_key": "nda", "answers": {}, "idempotency_key": "retry-key-0001"},
            ),
            headers={**HEADERS, "Authorization": KEY},
        )
    err = error_of(response)
    assert err["code"] == "outcome_unknown"
    assert err["idempotency_key"] == "retry-key-0001"
    assert "may or may not" in err["message"]


async def test_unexpected_host_is_refused(platform):
    """DNS-rebinding protection: a Host outside MCP_ALLOWED_HOSTS never reaches a tool."""
    async with running(fake(platform)) as http:
        response = await http.post(
            "/mcp", json=rpc("tools/list"), headers={**HEADERS, "Host": "evil.example.com"}
        )
    assert response.status_code in (400, 421)
    assert platform.calls == []


async def test_oversized_body_is_413(platform):
    """FR-55: a body over MCP_MAX_BODY_BYTES is refused before it is parsed."""
    config = Config(api_base_url=API, max_body_bytes=1000)
    async with running(fake(platform), config) as http:
        response = await http.post("/mcp", content=b"x" * 5000, headers=HEADERS)
    assert response.status_code == 413
    assert platform.calls == []


def test_redaction_filter_scrubs_keys_and_authorization_values():
    """FR-11: neither a key in a message, argument or traceback nor an Authorization value
    survives into a log line."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionFilter())
    logger = logging.getLogger("redaction-test")
    logger.propagate = False
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    secret = "clm_" + "Zz9_-" * 6
    try:
        logger.info("calling with %s", secret)
        logger.info("headers: {'authorization': 'Bearer %s'}", secret)
        logger.info("Authorization: Bearer abcdef.ghi")
        try:
            raise ValueError(f"boom {secret}")
        except ValueError:
            logger.exception("failed")
    finally:
        logger.removeHandler(handler)
    out = stream.getvalue()
    assert secret not in out
    assert "abcdef.ghi" not in out
    assert "clm_[REDACTED]" in out


def _access_record(*args: object) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', args, None
    )


def _format_like_uvicorn(record: logging.LogRecord) -> str:
    from uvicorn.logging import AccessFormatter

    RedactionFilter().filter(record)
    RedactionFilter().filter(record)  # idempotent: factory and handler filters both run it
    return AccessFormatter("%(levelprefix)s %(message)s", use_colors=False).format(record)


def test_filtered_uvicorn_access_record_still_formats():
    """Regression: the filter once nulled record.args, so uvicorn's AccessFormatter (which
    unpacks five positional args) failed on every access-log line, health checks included."""
    out = _format_like_uvicorn(_access_record("127.0.0.1:1234", "GET", "/readyz", "1.1", 200))
    assert '127.0.0.1:1234 - "GET /readyz HTTP/1.1" 200' in out


def test_access_record_args_with_keys_are_redacted_and_still_format():
    secret = "clm_" + "Qq7-" * 8
    out = _format_like_uvicorn(
        _access_record(f"Authorization: Bearer {secret}", "GET", f"/x?k={secret}", "1.1", 200)
    )
    assert secret not in out
    assert "Bearer clm" not in out
    assert "clm_[REDACTED]" in out
    assert out.rstrip().endswith("200")


def test_traceback_is_redacted_when_exc_info_is_set():
    secret = "clm_" + "Ab1_" * 8
    try:
        raise ValueError(f"bad {secret}")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    RedactionFilter().filter(record)
    text = logging.Formatter().format(record)
    assert secret not in text
    assert "clm_[REDACTED]" in text
