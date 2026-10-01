"""RAG-01: an answer is shown as an answer only when at least one of its quotes
verifies against the retrieved sources; fabricated citations don't count."""

from app.ai.schemas import BrainAnswerOutput, CitationInput
from app.contract_brain.grounding import ground_answer

SOURCE = (
    "Section 9. Limitation of Liability. Each party's aggregate liability under this Agreement "
    "shall not exceed the fees paid by Customer in the twelve months preceding the claim."
)


def _answer(*quotes):
    return BrainAnswerOutput(
        answer="Liability is capped at $5,000,000.",
        citations=[CitationInput(quote=q) for q in quotes],
        confidence="high",
    )


def test_answer_whose_citations_all_fail_to_verify_is_not_shown():
    out = ground_answer(
        _answer("Vendor's total liability is capped at USD 5,000,000", "Supplier indemnifies Customer without limit"),
        SOURCE,
    )
    assert (out["verified_citations"], out["total_citations"]) == (0, 2)
    assert "$5,000,000" not in out["display_answer"]
    assert out["confidence"] == "low"


def test_answer_with_a_verified_citation_is_kept():
    out = ground_answer(
        _answer("aggregate liability under this Agreement shall not exceed the fees paid by Customer"), SOURCE
    )
    assert out["verified_citations"] == 1
    assert out["display_answer"] == "Liability is capped at $5,000,000."


def test_answer_without_citations_shows_not_found():
    out = ground_answer(_answer(), SOURCE)
    assert out["display_answer"].startswith("I couldn't find contract text")
