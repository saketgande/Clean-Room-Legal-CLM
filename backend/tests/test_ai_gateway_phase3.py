"""Phase 3 of the AI gateway: the standalone agents call Claude through it.

DB-free checks on the agents whose inputs don't need Postgres (email triage,
notice extraction, notice reply). The intake agents (triage, form read, flow
router, litigation) use the identical call pattern and are verified live.
What each test pins: same prompt (+ guard), same budget, one ledger row, and
the agent's own fallback when the answer is missing, cut off or fails.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai.agent_catalog import UNTRUSTED_INPUT_GUARD
from app.ai.prompt_versions import DEFAULT_SKILL_PROMPTS
from app.core.config import settings
from tests.test_ai_gateway import (  # noqa: F401 — the autouse fixture applies here too
    FakeProvider,
    FakeSession,
    _ledger_uses_fake_sessions,
    _response,
)


@pytest.fixture(autouse=True)
def _real_ai_path(monkeypatch):
    monkeypatch.setattr(settings, "mock_claude", False)


def _system(key: str) -> str:
    return DEFAULT_SKILL_PROMPTS[key] + "\n\n" + UNTRUSTED_INPUT_GUARD


# --- email triage ----------------------------------------------------------------
def test_email_classification_runs_through_the_gateway():
    from app.intake.email_triage_agent import _llm_classify

    db = FakeSession()
    provider = FakeProvider(_response(
        tool="classify_email", tool_input={"is_clm_related": True, "category": "NDA", "confidence": 0.9},
    ))
    out = _llm_classify(db, "org-1", "NDA please", "Need an NDA with Acme", claude_client=provider)

    assert out["is_clm_related"] is True and out["confidence"] == 0.9
    sent = provider.calls[0][1]
    assert sent["system_prompt"] == _system("email_triage_agent")
    assert (sent["tool_name"], sent["max_tokens"], sent["temperature"]) == ("classify_email", 200, 0.0)
    [row] = db.rows
    assert row.prompt_key == "email_triage_agent" and row.status == "succeeded"
    assert row.input_payload["user_prompt"].startswith("EMAIL SUBJECT:")  # same as the old logger stored


@pytest.mark.parametrize(
    "provider",
    [
        FakeProvider(error=TimeoutError()),
        FakeProvider(_response(text="no tool call")),
        FakeProvider(_response(tool="classify_email", tool_input={"is_clm_related": True}, stop="max_tokens")),
    ],
    ids=["failed", "no-answer", "cut-off"],
)
def test_email_classification_falls_back_and_is_recorded(provider):
    from app.intake.email_triage_agent import _llm_classify

    db = FakeSession()
    assert _llm_classify(db, "org-1", "s", "t", claude_client=provider) is None  # caller uses the heuristic
    assert len(db.rows) == 1 and db.rows[0].status in {"failed", "validation_failed"}


# --- notice extraction -------------------------------------------------------------
def test_notice_extraction_runs_through_the_gateway():
    from app.notices.extraction import extract_notice_fields

    db = FakeSession()
    provider = FakeProvider(_response(
        tool="extract_notice_fields",
        tool_input={"counterparty_name": "Acme", "subject": "Breach", "notice_type": "breach", "confidence": 0.8},
    ))
    out = extract_notice_fields(db, org_id="org-1", text="NOTICE OF BREACH from Acme", claude_client=provider)

    assert out["source"] == "llm" and out["counterparty_name"] == "Acme"
    assert provider.calls[0][1]["system_prompt"] == _system("notice_extraction_agent")
    assert db.rows[0].input_payload == {"chars": len("NOTICE OF BREACH from Acme")}


def test_notice_extraction_cut_off_answer_uses_the_heuristic():
    from app.notices.extraction import extract_notice_fields

    db = FakeSession()
    provider = FakeProvider(_response(tool="extract_notice_fields", tool_input={"subject": "Bre"}, stop="max_tokens"))
    out = extract_notice_fields(db, org_id="org-1", text="NOTICE OF BREACH", claude_client=provider)
    assert out["source"] == "heuristic"
    assert db.rows[0].status == "validation_failed"


# --- notice reply (plain text) -----------------------------------------------------
NOTICE = SimpleNamespace(
    id="n-1", ref="NOT-1", subject="Breach", counterparty_name="Acme", counterparty_ref=None, direction="received",
    notice_type="breach", notice_date=None, response_due_date=None, description=None,
)


def test_notice_reply_runs_through_the_gateway():
    from app.notices.drafting import draft_notice_response

    db = FakeSession()
    provider = FakeProvider(_response(text="Dear Acme, ..."))
    out = draft_notice_response(db, org_id="org-1", notice=NOTICE, claude_client=provider)

    assert out == {"draft": "Dear Acme, ...", "generated": True}
    name, sent = provider.calls[0]
    assert name == "text" and sent["system_prompt"] == _system("notice_response_agent")
    assert (sent["max_tokens"], sent["temperature"]) == (1200, 0.2)
    [row] = db.rows
    assert (row.resource_type, row.resource_id, row.input_payload) == ("notice", "n-1", {"notice_id": "n-1"})


def test_notice_reply_empty_answer_uses_the_skeleton():
    from app.notices.drafting import draft_notice_response

    db = FakeSession()
    out = draft_notice_response(db, org_id="org-1", notice=NOTICE, claude_client=FakeProvider(_response(text="  ")))
    assert out["generated"] is False and out["draft"]
    assert db.rows[0].status == "validation_failed"
