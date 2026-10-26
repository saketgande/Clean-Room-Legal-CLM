"""What an API-key filing leaves behind, over real HTTP (feature 007, T008).

The audit trail must say HOW a request was filed (key vs. session, which key) and
the staff queue must not be fooled by caller-supplied source/name fields.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from app.core.models import AuditLog, ResourceTimelineEvent
from app.intake.models import IntakeRequest
from tests.test_intake_agent_identity import PREFIX, World, file_request


@pytest.fixture
def world():
    w = World()
    try:
        yield w
    finally:
        w.cleanup()


def _audits(world, resource_id, action):
    world.db.rollback()  # end any open read transaction so we see committed rows
    return list(world.db.scalars(select(AuditLog).where(
        AuditLog.resource_id == resource_id, AuditLog.action == action)))


def test_a_key_filing_forces_source_ignores_the_name_and_flags_the_agent(client, world):
    """AC-17: a key caller cannot pose as a web form or as the CEO. The stored
    source is `api`, the displayed name is the identity's registered name, and the
    audit row records the method and the key id."""
    agent = world.agent()
    res = file_request(client, agent, source="form", requester_name="CEO")
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["source"] == "api"
    assert body["requester_name"] == agent.full_name
    assert body["filed_by_agent"] is True and body["filed_by_agent_name"] == agent.full_name
    assert body["idempotent_repeat"] is False

    meta = _audits(world, body["id"], "intake.created")[0].metadata_json
    assert meta["auth_method"] == "api_key" and meta["api_key_id"] == agent.key_id
    assert meta["source"] == "api" and meta["filed_by_agent"] is True
    assert agent.key not in str(meta)

    title = world.db.scalar(select(ResourceTimelineEvent.title).where(
        ResourceTimelineEvent.resource_id == body["id"],
        ResourceTimelineEvent.event_type == "intake.created"))
    row = world.db.get(IntakeRequest, body["id"])
    assert row.filed_via_api_key_id == agent.key_id
    assert title == f"Request filed by agent {agent.full_name} — Contract Question"


def test_a_session_filing_is_audited_as_an_access_token_and_not_flagged(client, world):
    """AC-18: the same route by a signed-in person keeps what they sent."""
    agent = world.agent()
    res = file_request(client, world.jwt_headers(agent.user_id), source="copilot",
                       requester_name="Pat")
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["source"] == "copilot" and body["requester_name"] == "Pat"
    assert body["filed_by_agent"] is False and body["filed_by_agent_name"] is None
    meta = _audits(world, body["id"], "intake.created")[0].metadata_json
    assert meta["auth_method"] == "access_token" and meta["api_key_id"] is None
    assert meta["filed_by_agent"] is False


def test_add_document_by_key_audits_the_key(client, world):
    """AC-19."""
    agent = world.agent()
    rid = file_request(client, agent).json()["id"]
    res = client.post(f"{PREFIX}/intake/requests/{rid}/documents", headers=agent.headers,
                      json={"filename": "a.txt", "mime_type": "text/plain",
                            "content_b64": base64.b64encode(b"Extra terms.").decode()})
    assert res.status_code == 201, res.text
    rows = _audits(world, rid, "intake.document.added")
    assert len(rows) == 1
    assert rows[0].metadata_json == {"auth_method": "api_key", "api_key_id": agent.key_id}


def test_an_unsupported_or_oversized_document_is_refused_and_the_list_is_unchanged(client, world):
    """AC-16 (platform side): 415 for a type we do not accept, 413 over 25 MB; no
    document row and no audit row for either."""
    agent = world.agent()
    rid = file_request(client, agent).json()["id"]
    url = f"{PREFIX}/intake/requests/{rid}/documents"

    xlsx = base64.b64encode(b"PK\x03\x04 not really a spreadsheet").decode()
    res = client.post(url, headers=agent.headers, json={
        "filename": "budget.xlsx", "content_b64": xlsx,
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
    assert res.status_code == 415 and "budget.xlsx" in res.json()["detail"]

    big = base64.b64encode(b"x" * (25 * 1024 * 1024 + 1)).decode()
    res = client.post(url, headers=agent.headers, json={
        "filename": "big.txt", "mime_type": "text/plain", "content_b64": big})
    assert res.status_code == 413 and "big.txt" in res.json()["detail"]

    assert client.get(url, headers=agent.headers).json() == []
    assert _audits(world, rid, "intake.document.added") == []
