import hashlib
import logging
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select

from app.contract_files.models import ContractVersion, StorageObject
from app.contract_files.service import _read_upload_with_limit, create_contract_from_upload
from app.contracts.models import Contract
from app.core.config import settings
from app.integrations.claude import claude_client
from app.word_addin.schemas import (
    AskRequest,
    AskResponse,
    Finding,
    ReviewRequest,
    ReviewResponse,
)

logger = logging.getLogger(__name__)

# Hard cap on contract text per review. ~120k chars is roughly 30k tokens —
# comfortably inside the model context while leaving room for the response.
# Longer documents are trimmed and the response flags it.
_MAX_TEXT_CHARS = 120_000

_REVIEW_TOOL = "report_contract_review"

# Structured-output contract for the model. complete_structured() forces a
# tool call against this schema, so the response is already shaped like our
# Finding list — no brittle text parsing.
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "2-3 sentence plain-English read on the contract's risk posture.",
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short label, e.g. 'Uncapped liability'."},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "category": {"type": "string", "description": "e.g. Liability, IP, Termination, Data."},
                    "issue": {"type": "string", "description": "What's wrong and why it matters (1-3 sentences)."},
                    "original_text": {
                        "type": "string",
                        "description": (
                            "The EXACT text copied verbatim from the contract that should change, so an "
                            "editor can find and redline it. Leave empty for a missing clause."
                        ),
                    },
                    "suggested_text": {
                        "type": "string",
                        "description": "The proposed replacement wording, or the new clause to insert.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["replace", "insert", "flag"],
                        "description": (
                            "replace = swap original_text for suggested_text; "
                            "insert = add suggested_text (missing clause); flag = raise a concern only."
                        ),
                    },
                    "rationale": {"type": "string", "description": "One line on the negotiation rationale."},
                },
                "required": ["title", "severity", "issue", "action"],
            },
        },
    },
    "required": ["summary", "findings"],
}


def _system_prompt(party: str | None, playbook: str | None) -> str:
    lines = [
        "You are a senior commercial-contracts attorney reviewing a contract for risk.",
        "Surface the issues a careful lawyer would redline, ordered by severity.",
        "When there is concrete text to change, copy the EXACT original wording verbatim into "
        "original_text (so an editor can locate it) and put your proposal in suggested_text with "
        "action='replace'.",
        "For a clause that is missing entirely, use action='insert' with suggested_text and leave "
        "original_text empty.",
        "Use action='flag' only when raising a concern with no specific edit.",
        "Never invent text for original_text that is not present verbatim in the contract.",
    ]
    if party:
        lines.append(f"Represent this party's interests: {party}.")
    if playbook:
        lines.append("Apply this negotiation playbook / house positions:\n" + playbook.strip())
    return "\n".join(lines)


async def run_contract_review(req: ReviewRequest) -> ReviewResponse:
    text = req.text or ""
    truncated = len(text) > _MAX_TEXT_CHARS
    if truncated:
        text = text[:_MAX_TEXT_CHARS]

    user_prompt = (
        (f"Contract title: {req.title}\n\n" if req.title else "")
        + f"Return at most {req.max_findings} findings.\n\n"
        + "CONTRACT TEXT:\n"
        + text
    )

    # Stay under the per-request ceiling; 4k output tokens is plenty for a
    # dozen findings with quoted clauses.
    max_tokens = min(settings.claude_max_tokens_ceiling, 4000)

    response = await claude_client.complete_structured(
        system_prompt=_system_prompt(req.party, req.playbook),
        user_prompt=user_prompt,
        tool_name=_REVIEW_TOOL,
        input_schema=_INPUT_SCHEMA,
        max_tokens=max_tokens,
        temperature=settings.ai_default_temperature,
    )

    payload: dict[str, Any] = {}
    if response.tool_use_blocks:
        payload = response.tool_use_blocks[0].get("input") or {}

    findings: list[Finding] = []
    for item in (payload.get("findings") or [])[: req.max_findings]:
        try:
            findings.append(Finding(**item))
        except Exception:  # noqa: BLE001 — drop a malformed finding rather than 500 the whole review
            logger.warning("word_addin: dropping malformed finding: %s", item)

    return ReviewResponse(
        summary=payload.get("summary") or "Review complete.",
        findings=findings,
        model=response.model,
        truncated=truncated,
    )


_ASK_SYSTEM = (
    "You are Aegis, a senior commercial-contracts attorney embedded in Microsoft Word. "
    "Answer the user's question about the open contract precisely and concisely. Quote the "
    "relevant clause text when it helps. If the contract doesn't address the question, say so "
    "plainly. Use short paragraphs and bullet points where useful. This is not legal advice."
)


async def run_contract_question(req: AskRequest) -> AskResponse:
    text = req.text or ""
    truncated = len(text) > _MAX_TEXT_CHARS
    if truncated:
        text = text[:_MAX_TEXT_CHARS]

    user_prompt = (
        (f"Contract title: {req.title}\n\n" if req.title else "")
        + f"QUESTION:\n{req.question.strip()}\n\nCONTRACT TEXT:\n{text}"
    )

    response = await claude_client.complete_text(
        system_prompt=_ASK_SYSTEM,
        user_prompt=user_prompt,
        max_tokens=min(settings.claude_max_tokens_ceiling, 2000),
        temperature=settings.ai_default_temperature,
    )

    answer = "".join(
        block.get("text", "")
        for block in response.content_blocks
        if block.get("type") == "text"
    ).strip()

    return AskResponse(
        answer=answer or "I couldn't produce an answer for that.",
        model=response.model,
        truncated=truncated,
    )


def _stage_value(contract: Contract) -> str | None:
    stage = contract.lifecycle_stage
    return getattr(stage, "value", None) or (str(stage) if stage is not None else None)


async def resolve_contract_for_document(db, *, upload: UploadFile, user, request_id: str | None = None) -> dict:
    """Auto-link the open Word document to a contract.

    Matches the uploaded ``.docx`` to an existing contract by file content hash
    (so re-opening the same document links back to the same record); otherwise
    creates a new contract from it. Lets the add-in link silently on open with
    no manual step."""
    content = await _read_upload_with_limit(
        upload,
        limit=settings.max_upload_size_bytes,
        chunk_size=settings.upload_stream_chunk_bytes,
    )
    sha = hashlib.sha256(content).hexdigest()

    existing = db.scalars(
        select(StorageObject).where(
            StorageObject.org_id == user.org_id,
            StorageObject.sha256_hash == sha,
        )
    ).first()
    if existing is not None:
        version = db.scalars(
            select(ContractVersion).where(
                ContractVersion.org_id == user.org_id,
                ContractVersion.storage_object_id == existing.id,
                ContractVersion.deleted_at.is_(None),
            )
        ).first()
        if version is not None:
            contract = db.get(Contract, version.contract_id)
            if contract is not None and contract.deleted_at is None:
                return {
                    "contract_id": contract.id,
                    "title": contract.title,
                    "lifecycle_stage": _stage_value(contract),
                    "created": False,
                }

    # No match — create a new contract from the document. Reset the stream
    # first since we already consumed it to hash the bytes.
    await upload.seek(0)
    result = await create_contract_from_upload(db, upload=upload, user=user, request_id=request_id)
    contract = result["contract"]
    return {
        "contract_id": contract.id,
        "title": contract.title,
        "lifecycle_stage": _stage_value(contract),
        "created": True,
    }
