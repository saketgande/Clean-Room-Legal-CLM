"""Notice response drafting agent.

Produces a first draft of a reply to a received notice, grounded in the notice
record and the text of whatever was actually served.

The output is explicitly a *draft for a lawyer to edit*, never something the
system sends. Two guardrails make that real rather than aspirational:

* The prompt forbids conceding liability, agreeing amounts, or inventing facts
  the file doesn't contain — the failure mode for a generated legal reply isn't
  clumsy prose, it's an admission nobody intended to make.
* It never raises. Under mock mode or an API failure it returns a skeleton
  reply with the notice's own particulars filled in, so the lawyer always has a
  starting structure even when the model is unavailable.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Notices are short; the operative demands sit in the opening and the prayer
# clause. This is plenty without paying for an exhibit bundle.
_MAX_DOC_CHARS = 10000


def _skeleton(notice) -> str:
    """Deterministic fallback: a correctly-structured holding reply. Says
    nothing substantive on purpose — a holding response that concedes nothing is
    always safe to send; a generated argument is not."""
    ref = notice.counterparty_ref or notice.ref
    return (
        f"Dear Sirs,\n\n"
        f"RE: {notice.subject}\n"
        f"Your reference: {ref}\n\n"
        f"We acknowledge receipt of your notice"
        + (f" dated {notice.notice_date}" if notice.notice_date else "")
        + ".\n\n"
        "The contents of your notice are noted and are under review. Our client's "
        "rights and remedies are fully reserved, and nothing in this letter should "
        "be construed as an admission of liability or as a waiver of any right.\n\n"
        "We will revert substantively in due course.\n\n"
        "Yours faithfully,\n[Name]\n[Title]"
    )


def _prompt(notice, document_text: str) -> str:
    facts = [
        f"Notice reference: {notice.ref}",
        f"Type: {notice.notice_type}",
        f"From: {notice.counterparty_name}",
        f"Subject: {notice.subject}",
    ]
    if notice.counterparty_ref:
        facts.append(f"Their reference: {notice.counterparty_ref}")
    if notice.notice_date:
        facts.append(f"Dated: {notice.notice_date}")
    if notice.response_due_date:
        facts.append(f"Response due: {notice.response_due_date}")
    if notice.description:
        facts.append(f"What it demands: {notice.description}")

    body = "\n".join(facts)
    if document_text:
        body += (
            "\n\n--- TEXT OF THE NOTICE AS SERVED ---\n"
            f"{document_text[:_MAX_DOC_CHARS]}\n--- END ---"
        )
    return (
        "Draft a reply to the legal notice below, on behalf of the company that "
        "received it.\n\n" + body
    )


def draft_notice_response(db: Session, *, org_id: str, notice, document_text: str = "") -> dict:
    """Draft a reply. Always returns {'draft': str, 'generated': bool}; the
    fallback skeleton is used whenever the model is unavailable."""
    from app.core.config import settings

    if settings.mock_claude:
        return {"draft": _skeleton(notice), "generated": False}

    from app.ai.agent_catalog import UNTRUSTED_INPUT_GUARD, get_agent_prompt, log_agent_call
    from app.ai.cost_guard import enforce_daily_token_cap
    from app.integrations.claude import ClaudeClient, run_coro_blocking

    bundle = get_agent_prompt(db, agent_id="notice_response_agent", org_id=org_id)
    try:
        enforce_daily_token_cap(org_id)
        resp = run_coro_blocking(lambda: ClaudeClient().complete_text(
            system_prompt=bundle.skill_prompt + "\n\n" + UNTRUSTED_INPUT_GUARD,
            user_prompt=_prompt(notice, document_text),
            max_tokens=1200, temperature=0.2, model=bundle.model_name,
        ))
        log_agent_call(
            db, org_id=org_id, agent_id="notice_response_agent", prompt_bundle=bundle,
            input_payload={"notice_id": notice.id}, response=resp,
        )
        text = "".join(
            b.get("text", "") for b in resp.content_blocks if b.get("type") == "text"
        ).strip()
        if not text:
            return {"draft": _skeleton(notice), "generated": False}
        return {"draft": text, "generated": True}
    except Exception:
        logger.warning(
            "notice_response_agent failed for %s — using skeleton reply",
            notice.id, exc_info=True,
        )
        return {"draft": _skeleton(notice), "generated": False}
