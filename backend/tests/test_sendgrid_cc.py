"""SENDGRID_CC_EMAIL is copied on every outbound SendGrid send, except when
it would duplicate the primary recipient (case-insensitive compare)."""

from __future__ import annotations

from typing import ClassVar

import pytest

from app.core.config import settings
from app.integrations.sendgrid import SendGridClient


class _FakeResponse:
    status_code = 202
    headers: ClassVar[dict] = {"X-Message-Id": "fake-id"}

    def raise_for_status(self) -> None:
        return None


class _FakeHttpClient:
    def __init__(self) -> None:
        self.last_json: dict | None = None

    async def post(self, url: str, *, headers: dict, json: dict) -> _FakeResponse:
        self.last_json = json
        return _FakeResponse()


@pytest.fixture
def fake_client(monkeypatch):
    fake = _FakeHttpClient()
    monkeypatch.setattr("app.integrations.sendgrid._sendgrid_client", lambda: fake)
    monkeypatch.setattr(settings, "mock_sendgrid", False)
    monkeypatch.setattr(settings, "sendgrid_api_key", "fake-key")
    yield fake


async def test_send_email_ccs_the_configured_address(fake_client, monkeypatch):
    monkeypatch.setattr(settings, "sendgrid_cc_email", "akhil.pulagura@in.ey.com")
    client = SendGridClient()

    await client.send_email(to="someone@example.com", subject="hi", html="<p>hi</p>")

    personalization = fake_client.last_json["personalizations"][0]
    assert personalization["to"] == [{"email": "someone@example.com"}]
    assert personalization["cc"] == [{"email": "akhil.pulagura@in.ey.com"}]


async def test_send_email_skips_cc_when_it_matches_the_recipient(fake_client, monkeypatch):
    monkeypatch.setattr(settings, "sendgrid_cc_email", "Someone@Example.com")
    client = SendGridClient()

    await client.send_email(to="someone@example.com", subject="hi", html="<p>hi</p>")

    personalization = fake_client.last_json["personalizations"][0]
    assert "cc" not in personalization


async def test_send_email_omits_cc_when_unconfigured(fake_client, monkeypatch):
    monkeypatch.setattr(settings, "sendgrid_cc_email", None)
    client = SendGridClient()

    await client.send_email(to="someone@example.com", subject="hi", html="<p>hi</p>")

    personalization = fake_client.last_json["personalizations"][0]
    assert "cc" not in personalization
