from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contract_files.models import (
    ContractDocumentElement,
    ContractTextSnapshot,
    ContractVersion,
)
from app.contract_files.structure import distinctive_tokens, word_set
from app.contracts.models import Contract

MAX_FULL_TEXT_CHARS = 25_000

# Clause-scoped retrieval (Phase 3). When an instruction is supplied, send only
# the clauses it's about instead of the whole (truncated) document.
_FOCUS_TOP_K = 8
_FOCUS_MAX_CHARS = 8_000
_FOCUS_COVERAGE = 0.5  # the chosen clauses must together cover half the query's topic words
_SEM_ABS_FLOOR = 0.55  # semantic fallback: minimum cosine to trust a clause match
_SEM_MARGIN = 0.11     # ...and it must stand out this far above the median clause,
# which separates a real synonym match from a vague instruction that's mildly
# related to everything. The margin is deliberately conservative: when unsure we
# fall back to the full document rather than risk focusing on the wrong clause.


def _focused_text(db: Session, snapshot: ContractTextSnapshot | None, query: str) -> str | None:
    """Return just the clauses relevant to ``query`` (each with its heading for
    context), or None to signal 'not confident — use the full text'. Only
    engages for structured snapshots; retrieval finds the right clause wherever
    it sits, so the answer no longer depends on the clause being in the first
    25k characters."""
    if snapshot is None or snapshot.structure_status != "structured":
        return None
    els = db.scalars(
        select(ContractDocumentElement)
        .where(ContractDocumentElement.text_snapshot_id == snapshot.id)
        .order_by(ContractDocumentElement.seq)
    ).all()
    if not els:
        return None

    qt = distinctive_tokens(query)
    body = [(i, e) for i, e in enumerate(els) if e.element_type != "page_artifact"]
    if not body:
        return None

    picks: list[int] = []

    # 1) Lexical: precise topic-word match. Fast, and handles keyword
    #    instructions. Engage only if the matched clauses together ground enough
    #    of the instruction ("liability AND affiliate" is covered by two clauses;
    #    "make it better" covers nothing).
    if qt:
        lex = {i: (qt & word_set(e.text)) for i, e in body}
        lex = {i: h for i, h in lex.items() if h}
        if lex and len(set().union(*lex.values())) / len(qt) >= _FOCUS_COVERAGE:
            picks = sorted(lex, key=lambda i: len(lex[i]), reverse=True)[:_FOCUS_TOP_K]

    # 2) Semantic fallback: only when lexical wasn't confident — catches
    #    synonyms/paraphrases ("cap the damages" ~ "Limitation of Liability")
    #    that share no words. One embedding pass over query + clauses; returns []
    #    if embeddings are unavailable, so we simply fall back to full text.
    if not picks:
        from app.ai.embeddings import similarities

        sims = similarities(query, [e.text for _, e in body])
        if sims:
            median = sorted(sims)[len(sims) // 2]
            floor = max(_SEM_ABS_FLOOR, median + _SEM_MARGIN)
            sem = sorted(
                (bi for bi, s in enumerate(sims) if s >= floor),
                key=lambda bi: sims[bi],
                reverse=True,
            )[:_FOCUS_TOP_K]
            picks = [body[bi][0] for bi in sem]  # map back to full-element index

    if not picks:
        return None

    keep: set[int] = set(picks)
    for i in list(picks):
        for j in range(i - 1, -1, -1):  # nearest preceding heading, for context
            if els[j].element_type in ("heading", "title"):
                keep.add(j)
                break

    out: list[str] = []
    total = 0
    for i in sorted(keep):
        # e.text already begins with its clause number, so it's sent verbatim —
        # the model's quotes then match the full document exactly for anchoring.
        chunk = els[i].text
        if total + len(chunk) > _FOCUS_MAX_CHARS:
            break
        out.append(chunk)
        total += len(chunk)
    return "\n\n".join(out) if out else None


@dataclass(frozen=True)
class ContractAIContext:
    contract: Contract
    version: ContractVersion | None
    snapshot: ContractTextSnapshot | None
    text: str
    text_was_truncated: bool
    manifest: dict


def build_contract_context(
    db: Session,
    *,
    org_id: str,
    contract_id: str,
    contract_version_id: str | None = None,
    text_snapshot_id: str | None = None,
    focus_query: str | None = None,
) -> ContractAIContext:
    contract = db.get(Contract, contract_id)
    if contract is None or contract.org_id != org_id or contract.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")

    version = None
    if contract_version_id:
        version = db.get(ContractVersion, contract_version_id)
    elif contract.current_authoritative_version_id:
        version = db.get(ContractVersion, contract.current_authoritative_version_id)

    if version is not None and (version.org_id != org_id or version.contract_id != contract_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract version not found")

    snapshot = None
    if text_snapshot_id:
        snapshot = db.get(ContractTextSnapshot, text_snapshot_id)
    elif version is not None and version.text_snapshot_id:
        snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id)

    if snapshot is not None and (snapshot.org_id != org_id or snapshot.contract_id != contract_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract text snapshot not found")

    text = snapshot.text if snapshot else ""

    # Clause-scoped path: if the caller supplied a focus query and the snapshot
    # is structured, send only the relevant clauses. This finds the right clause
    # wherever it sits — so a large contract is no longer silently cut off at 25k
    # — and sends a fraction of the tokens. Falls back to the truncated full text
    # whenever retrieval isn't confident, so it's never worse than before.
    focused = _focused_text(db, snapshot, focus_query) if focus_query else None
    if focused is not None:
        payload_text = focused
        was_truncated = False
        focus_applied = True
    else:
        payload_text = text[:MAX_FULL_TEXT_CHARS]
        was_truncated = len(text) > len(payload_text)
        focus_applied = False

    return ContractAIContext(
        contract=contract,
        version=version,
        snapshot=snapshot,
        text=payload_text,
        text_was_truncated=was_truncated,
        manifest={
            "contract_id": contract.id,
            "contract_version_id": version.id if version else None,
            "text_snapshot_id": snapshot.id if snapshot else None,
            "text_length": len(text),
            "text_was_truncated": was_truncated,
            "focus_applied": focus_applied,
            # Real deal facts already computed elsewhere in the app (metadata
            # extraction, risk assessment) — without these, a skill has nothing
            # to reason from but the raw clause text, so it defaults to generic
            # textbook explanations instead of judgment grounded in this deal.
            "title": contract.title,
            "contract_type": contract.contract_type,
            "counterparty_name": contract.counterparty_name,
            "jurisdiction": contract.jurisdiction,
            "value_amount": contract.value_amount,
            "currency": contract.currency,
            "risk_band": contract.risk_band,
            "risk_summary": (contract.risk_summary or {}).get("summary"),
        },
    )


def list_contract_handles(db: Session, *, session_id: str) -> list[dict]:
    from app.assistant.models import AssistantContractHandle

    handles = db.scalars(
        select(AssistantContractHandle).where(AssistantContractHandle.session_id == session_id)
    ).all()
    return [
        {
            "handle": handle.handle,
            "contract_id": handle.contract_id,
            "metadata": handle.metadata_json,
        }
        for handle in handles
    ]


def chunk_text(text: str, *, chunk_chars: int = 3600, overlap_chars: int = 500) -> list[dict]:
    if not text:
        return []
    chunks = []
    start = 0
    index = 0
    while start < len(text):
        end = min(start + chunk_chars, len(text))
        chunks.append({"chunk_index": index, "start_char": start, "end_char": end, "text": text[start:end]})
        if end == len(text):
            break
        start = max(0, end - overlap_chars)
        index += 1
    return chunks
