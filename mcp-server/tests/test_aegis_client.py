"""The platform client. Failure modes guarded: calls outside the nine allowed endpoints, the
caller's key leaking into shared client state or following a redirect, and retries that
double-file or re-send on the wrong statuses."""

from __future__ import annotations

import httpx
import pytest
from conftest import KEY, FakePlatform

from aegis_intake_mcp.aegis_client import (
    ALLOWED_CALLS,
    AegisClient,
    ClientPolicyError,
    UpstreamUnavailable,
)
from aegis_intake_mcp.config import Config


def test_allow_list_is_exactly_the_nine_pairs():
    assert set(ALLOWED_CALLS) == {
        ("GET", "/intake/forms"),
        ("GET", "/legal-entities"),
        ("GET", "/counterparties"),
        ("POST", "/intake/requests"),
        ("GET", "/intake/requests/mine"),
        ("GET", "/intake/requests/{id}"),
        ("GET", "/intake/requests/{id}/sla"),
        ("GET", "/intake/requests/{id}/documents"),
        ("POST", "/intake/requests/{id}/documents"),
    }
    assert [k for k, retry in ALLOWED_CALLS.items() if not retry] == [
        ("POST", "/intake/requests/{id}/documents")
    ]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/intake/requests"),  # staff queue
        ("DELETE", "/intake/requests/{id}"),
        ("PUT", "/intake/requests/{id}"),
        ("GET", "/contracts"),
        ("GET", "/users/api-keys"),
        ("POST", "/intake/forms"),
        ("GET", "http://evil.example/intake/forms"),
        ("GET", "//evil.example/intake/forms"),
        ("GET", "intake/forms"),
    ],
)
async def test_non_allow_listed_calls_make_no_request(client, platform, method, path):
    kwargs = {"path_params": {"id": "abc"}} if "{id}" in path else {}
    with pytest.raises(ClientPolicyError):
        await client.request(method, path, auth_header=KEY, **kwargs)
    assert platform.calls == []


@pytest.mark.parametrize("bad", ["../x", "a/b", "a b", "", "x" * 65, "a?b=1", "%2e%2e", "ä"])
async def test_unsafe_path_params_rejected(client, platform, bad):
    with pytest.raises(ClientPolicyError):
        await client.request(
            "GET", "/intake/requests/{id}", auth_header=KEY, path_params={"id": bad}
        )
    assert platform.calls == []


async def test_path_param_mismatch_rejected(client, platform):
    with pytest.raises(ClientPolicyError):
        await client.request("GET", "/intake/requests/{id}", auth_header=KEY)
    with pytest.raises(ClientPolicyError):
        await client.request(
            "GET", "/intake/forms", auth_header=KEY, path_params={"id": "abc"}
        )
    assert platform.calls == []


async def test_path_param_is_placed_in_the_url(client, platform):
    platform.on("GET", "/intake/requests/6f1c-2a", json={"id": "6f1c-2a"})
    r = await client.request(
        "GET", "/intake/requests/{id}", auth_header=KEY, path_params={"id": "6f1c-2a"}
    )
    assert r.json() == {"id": "6f1c-2a"}
    assert str(platform.calls[0].url) == "http://backend:8000/api/v1/intake/requests/6f1c-2a"


@pytest.mark.parametrize("header", ["", "Bearer x\r\nX-Evil: 1", "Bearer x\ny"])
async def test_bad_auth_header_rejected(client, platform, header):
    with pytest.raises(ClientPolicyError):
        await client.request("GET", "/intake/forms", auth_header=header)
    assert platform.calls == []


async def test_no_default_authorization_header(config, platform):
    c = AegisClient(config, transport=httpx.MockTransport(platform.handler))
    try:
        assert "authorization" not in {k.lower() for k in c._http.headers}
        assert c._http.trust_env is False
        assert c._http.follow_redirects is False
    finally:
        await c.aclose()
    assert not any(KEY in str(v) for v in vars(c).values())


async def test_per_call_header_is_forwarded_and_not_shared(client, platform):
    platform.on("GET", "/intake/forms", json=[])
    other = "Bearer clm_" + "b" * 32
    await client.request("GET", "/intake/forms", auth_header=KEY)
    await client.request("GET", "/intake/forms", auth_header=other)
    assert [c.headers["authorization"] for c in platform.calls] == [KEY, other]
    assert not any("authorization" in k.lower() for k in client._http.headers)


async def test_query_params_and_json_body_forwarded(client, platform):
    platform.on("GET", "/counterparties", json=[])
    platform.on("POST", "/intake/requests", status=201, json={"id": "r1"})
    await client.request(
        "GET", "/counterparties", auth_header=KEY, params={"q": "acme", "limit": 50}
    )
    await client.request("POST", "/intake/requests", auth_header=KEY, json={"a": 1})
    assert platform.calls[0].url.query == b"q=acme&limit=50"
    assert platform.calls[1].content == b'{"a":1}'


async def test_redirects_are_not_followed(client, platform):
    """A 302 must come back as-is; following it would resend the Bearer key elsewhere."""
    platform.on(
        "GET", "/intake/forms",
        httpx.Response(302, headers={"location": "http://evil.example/steal"}),
    )
    r = await client.request("GET", "/intake/forms", auth_header=KEY)
    assert r.status_code == 302
    assert len(platform.calls) == 1


@pytest.mark.parametrize(
    "first",
    [
        httpx.ConnectError("boom"),
        httpx.ReadTimeout("slow"),
        httpx.Response(502),
        httpx.Response(503),
        httpx.Response(504),
    ],
)
async def test_read_retries_once_on_transient_failure(client, platform, first):
    platform.on("GET", "/intake/forms", first, httpx.Response(200, json=["ok"]))
    r = await client.request("GET", "/intake/forms", auth_header=KEY)
    assert r.json() == ["ok"]
    assert len(platform.calls) == 2
    assert platform.calls[0].headers["authorization"] == platform.calls[1].headers["authorization"]


async def test_create_retry_resends_identical_body(client, platform):
    platform.on(
        "POST", "/intake/requests",
        httpx.ReadTimeout("slow"), httpx.Response(201, json={"id": "r1"}),
    )
    body = {"idempotency_key": "mcp-" + "0" * 32, "subject": "s"}
    r = await client.request("POST", "/intake/requests", auth_header=KEY, json=body)
    assert r.status_code == 201
    assert len(platform.calls) == 2
    assert platform.calls[0].content == platform.calls[1].content


async def test_double_failure_raises_after_two_attempts(client, platform):
    platform.on("GET", "/intake/forms", httpx.ConnectError("down"))
    with pytest.raises(UpstreamUnavailable) as info:
        await client.request("GET", "/intake/forms", auth_header=KEY)
    assert info.value.reason == "connect"
    assert len(platform.calls) == 2


async def test_repeated_503_raises_with_status(client, platform):
    platform.on("GET", "/intake/forms", status=503, json={})
    with pytest.raises(UpstreamUnavailable) as info:
        await client.request("GET", "/intake/forms", auth_header=KEY)
    assert (info.value.reason, info.value.status_code) == ("status", 503)
    assert len(platform.calls) == 2


async def test_timeout_reason_is_reported(client, platform):
    platform.on("GET", "/intake/forms", httpx.ReadTimeout("slow"))
    with pytest.raises(UpstreamUnavailable) as info:
        await client.request("GET", "/intake/forms", auth_header=KEY)
    assert info.value.reason == "timeout"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 415, 422, 429, 500])
async def test_non_transient_statuses_are_never_retried(client, platform, status):
    platform.on("GET", "/intake/forms", status=status, json={"detail": "x"})
    r = await client.request("GET", "/intake/forms", auth_header=KEY)
    assert r.status_code == status
    assert len(platform.calls) == 1


@pytest.mark.parametrize("first", [httpx.ReadTimeout("slow"), httpx.Response(503)])
async def test_add_document_never_retries(client, platform, first):
    platform.on("POST", "/intake/requests/abc/documents", first, httpx.Response(201, json={}))
    with pytest.raises(UpstreamUnavailable):
        await client.request(
            "POST", "/intake/requests/{id}/documents", auth_header=KEY,
            path_params={"id": "abc"}, json={"filename": "a"},
        )
    assert len(platform.calls) == 1


async def test_attempts_setting_is_honoured():
    platform = FakePlatform().on("GET", "/intake/forms", httpx.ConnectError("down"))
    c = AegisClient(
        Config(attempts=3), transport=httpx.MockTransport(platform.handler),
        retry_backoff_seconds=0,
    )
    try:
        with pytest.raises(UpstreamUnavailable):
            await c.request("GET", "/intake/forms", auth_header=KEY)
    finally:
        await c.aclose()
    assert len(platform.calls) == 3


async def test_write_calls_get_the_longer_timeout(config):
    seen: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.method] = request.extensions["timeout"]
        return httpx.Response(200, json={})

    c = AegisClient(config, transport=httpx.MockTransport(handler))
    try:
        await c.request("GET", "/intake/forms", auth_header=KEY)
        await c.request("POST", "/intake/requests", auth_header=KEY, json={})
    finally:
        await c.aclose()
    assert seen["GET"]["read"] == 15.0 and seen["POST"]["read"] == 45.0
    assert seen["GET"]["connect"] == seen["POST"]["connect"] == 5.0
