"""Phase 2 of the AI gateway: the three features that used to call Claude
directly, with hardcoded prompts and no ledger row, now go through the gateway.

DB-free: the fakes from test_ai_gateway stand in for Postgres and Claude.
Each test checks the same four things — the prompt Claude receives is the one
the module used to hardcode (plus the guard), the call is recorded, the
feature's result is unchanged, and a failure still falls back as before.
"""

from __future__ import annotations

import asyncio
import json
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
    # These features short-circuit to their heuristics under mock mode; the
    # fake provider below is what stands in for Claude here.
    monkeypatch.setattr(settings, "mock_claude", False)


def _system(key: str) -> str:
    return DEFAULT_SKILL_PROMPTS[key] + "\n\n" + UNTRUSTED_INPUT_GUARD


# --- renewal recommendation --------------------------------------------------
RENEWAL = SimpleNamespace(id="ren-1", notice_date=None, expiration_date=None)
CONTRACT = SimpleNamespace(
    id="c-1", title="MSA — Globex", contract_type="MSA", counterparty_name="Globex",
    value_amount=None, risk_band="high", risk_score=None,
)


def test_renewal_recommendation_is_recorded_and_returned():
    from app.renewals.recommendation import recommend_renewal

    db = FakeSession()
    answer = {"decision": "renegotiate", "rationale": "High risk on file.", "confidence": "medium"}
    provider = FakeProvider(_response(tool="recommend_renewal", tool_input=answer))

    out = recommend_renewal(db, org_id="org-1", renewal=RENEWAL, contract=CONTRACT, claude_client=provider)

    assert out == {**answer, "generated": True}
    sent = provider.calls[0][1]
    assert sent["system_prompt"] == _system("renewal_recommendation")
    assert (sent["tool_name"], sent["max_tokens"], sent["temperature"]) == ("recommend_renewal", 400, 0.2)
    assert "Contract: MSA — Globex" in sent["user_prompt"]
    [row] = db.rows
    assert (row.prompt_key, row.status, row.resource_type, row.resource_id) == (
        "renewal_recommendation", "succeeded", "renewal_event", "ren-1",
    )


def test_renewal_failure_falls_back_and_is_still_recorded():
    from app.renewals.recommendation import recommend_renewal

    db = FakeSession()
    out = recommend_renewal(
        db, org_id="org-1", renewal=RENEWAL, contract=CONTRACT,
        claude_client=FakeProvider(error=TimeoutError()),
    )
    assert out["generated"] is False and out["decision"] == "renegotiate"  # high-risk heuristic
    assert db.rows[0].status == "failed" and db.rows[0].error_class == "TimeoutError"


# --- playbook expansion -------------------------------------------------------
def test_playbook_expansion_is_recorded_and_keeps_only_requested_clauses():
    from app.playbooks.service import PlaybooksService

    db = FakeSession()
    rules = [
        {"clause_type": "confidentiality", "preferred_position": "Mutual", "risk_level": "low", "rationale": "x"},
        {"clause_type": "not_requested", "preferred_position": "?", "risk_level": "low", "rationale": "y"},
    ]
    provider = FakeProvider(_response(tool="draft_playbook_rules", tool_input={"rules": rules}))
    playbook = SimpleNamespace(id="pb-1", name="NDA", description=None)

    out, generated = PlaybooksService(db, claude_client=provider)._generate_missing_rules(
        org_id="org-1", playbook=playbook, covered=["termination"], missing=["confidentiality"],
    )

    assert generated is True and [r["clause_type"] for r in out] == ["confidentiality"]
    sent = provider.calls[0][1]
    assert sent["system_prompt"] == _system("playbook_expand")
    assert (sent["tool_name"], sent["max_tokens"], sent["temperature"]) == ("draft_playbook_rules", 2500, 0.3)
    [row] = db.rows
    assert (row.prompt_key, row.resource_type, row.resource_id) == ("playbook_expand", "playbook", "pb-1")
    assert row.input_payload == {"playbook_id": "pb-1", "missing": 1, "covered": 1}


def test_playbook_expansion_bad_answer_falls_back_to_templates():
    from app.playbooks.service import PlaybooksService

    db = FakeSession()
    provider = FakeProvider(_response(tool="draft_playbook_rules", tool_input={"rules": []}))
    out, generated = PlaybooksService(db, claude_client=provider)._generate_missing_rules(
        org_id="org-1", playbook=SimpleNamespace(id="pb-1", name="NDA", description=None),
        covered=[], missing=["confidentiality"],
    )
    assert generated is False and out  # deterministic templates
    assert db.rows[0].status == "succeeded"  # the call itself worked; the caller rejected the empty list


# --- trademark journal vision ---------------------------------------------------
def _one_page_pdf() -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "TRADE MARKS JOURNAL")
    try:
        return doc.tobytes()
    finally:
        doc.close()


def test_trademark_page_read_is_recorded_and_parsed():
    from app.trademarks.extraction.vision import extract_journal_page

    entry = {
        "product_name": "ACME", "mark_type": "word", "tm_id": "123", "tm_date": "01/01/2024",
        "address": "Acme Ltd", "used_since": None, "proposed_to_be_used": True,
        "jurisdiction": "MUMBAI", "goods_services": "Soap", "has_product_image": False,
    }
    db = FakeSession()
    provider = FakeProvider(_response(tool="extract_journal_entries", tool_input={"entries": [entry]}))

    entries = asyncio.run(extract_journal_page(db, _one_page_pdf(), 1, org_id="org-1", claude_client=provider))

    assert [e.tm_id for e in entries] == ["123"]
    name, sent = provider.calls[0]
    assert name == "vision" and sent["image_media_type"] == "image/png"
    assert sent["system_prompt"] == _system("trademark_journal_vision")
    assert (sent["max_tokens"], sent["temperature"]) == (4000, 0.0)
    [row] = db.rows
    assert row.prompt_key == "trademark_journal_vision" and row.input_payload["page_number"] == 1


def test_trademark_page_without_an_answer_fails_loudly_and_is_recorded():
    """It used to return [] — indistinguishable from a page with no marks. Now
    the caller reports the page as failed (extract_fields adds a warning)."""
    from app.ai.gateway import AIOutputInvalid
    from app.trademarks.extraction.vision import extract_journal_page

    db = FakeSession()
    with pytest.raises(AIOutputInvalid):
        asyncio.run(extract_journal_page(
            db, _one_page_pdf(), 1, org_id="org-1", claude_client=FakeProvider(_response(text="no tool")),
        ))
    assert db.rows[0].status == "validation_failed"


def test_moved_prompts_are_byte_for_byte_what_the_modules_had():
    """Hashes of the prompt texts as they were hardcoded before Phase 2."""
    import hashlib

    expected = json.loads(EXPECTED_HASHES)
    for key, digest in expected.items():
        assert hashlib.sha256(DEFAULT_SKILL_PROMPTS[key].encode()).hexdigest() == digest, key


EXPECTED_HASHES = '{"trademark_journal_vision": "dd1d8a0f57ee9fbbac6d85e91b1d32feb563cb30431428ebafd9ff79c0b9b5da", "renewal_recommendation": "02a7b2958efb60151bb35cb8803bd2cf7928f3bf863212f12dbfb69ba7ea3f51", "playbook_expand": "82f5d163c131f5dfcb3b59aa979652c762c2b2c9a1917b3f4f87f99a981560d8"}'
