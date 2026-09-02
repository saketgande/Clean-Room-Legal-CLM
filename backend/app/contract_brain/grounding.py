"""Shared grounding for Contract Brain answers.

Both the Brain page (`POST /contract-brain/ask`) and the assistant's
`ask_contract_brain` tool run a model answer through this one function, so they
give the *same* verified, cited result. Previously the route had this logic
inline and the assistant tool had none — it returned raw retrieval and let the
model assert whatever it liked.

The contract: verify, then attribute.
- Snap every model quote to the closest REAL span in the retrieved sources and
  show that actual span (alignment, not trust).
- Cap the answer's confidence by how much of it verifies.
- If nothing verifies, replace the prose with an honest "not found" — a reader
  skimming a confident answer over a caveat box shouldn't walk away trusting
  fabricated specifics.
"""

from app.ai.citations import align_citation_to_source
from app.ai.schemas import BrainAnswerOutput

# A citation must align to a real source span at ≥ this score to count as
# grounded. Distinct from the stricter per-skill validate_citations threshold —
# here we're aligning a paraphrased quote to a window, not exact-matching.
GROUNDING_THRESHOLD = 85.0


def ground_answer(answer: BrainAnswerOutput, source_text: str) -> dict:
    """Verify a Brain answer against its retrieved sources.

    Returns a dict with the display-safe answer, per-citation validation, a
    grounding-adjusted confidence, and the metadata both callers persist:
    ``display_answer, citations, confidence, model_confidence, citation_review,
    grounding, verified_citations, total_citations, limitations``.
    """
    validated_citations: list[dict] = []
    review = "valid"
    valid_cites = 0
    for c in answer.citations:
        span, score = align_citation_to_source(c.quote, source_text)
        is_valid = score >= GROUNDING_THRESHOLD
        if is_valid:
            valid_cites += 1
        else:
            review = "needs_review"
        validated_citations.append(
            {
                "quote": span if is_valid else c.quote,
                "label": c.label,
                "validation_status": "valid" if is_valid else "invalid",
                "similarity_score": round(score, 1),
            }
        )
    if not source_text:
        review = "no_context"

    total_cites = len(answer.citations)
    grounding = (valid_cites / total_cites) if total_cites else 0.0
    confidence = answer.confidence
    if total_cites == 0 or grounding < 0.5:
        confidence = "low"
    elif grounding < 0.8 and confidence == "high":
        confidence = "medium"

    display_answer = answer.answer
    if total_cites == 0:
        display_answer = (
            "I couldn't find contract text in the retrieved sources that directly "
            "supports a specific answer to this question."
            + (f" {answer.limitations}" if answer.limitations else "")
        )

    return {
        "display_answer": display_answer,
        "citations": validated_citations,
        "confidence": confidence,
        "model_confidence": answer.confidence,
        "citation_review": review,
        "grounding": round(grounding, 3),
        "verified_citations": valid_cites,
        "total_citations": total_cites,
        "limitations": answer.limitations,
    }
