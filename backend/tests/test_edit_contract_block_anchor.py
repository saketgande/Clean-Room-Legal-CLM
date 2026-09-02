from app.ai.tool_runtime import _anchor_suggestions

DOC = (
    "1. Term. This Agreement begins on the Effective Date and continues for one year.\n\n"
    "2. Payment. Fees are due within thirty (30) days of invoice.\n\n"
    "3. Confidentiality. Each party shall protect the other's Confidential Information."
)


class _Suggestion:
    def __init__(self, original, replacement):
        self.original_text = original
        self.replacement_text = replacement
        self.edit_type = "replace"
        self.rationale = None
        self.risk_level = "medium"
        self.citations = []


def test_paraphrased_quote_anchors_to_block_instead_of_being_dropped():
    # A quote the model paraphrased so it does NOT appear verbatim — exact,
    # whitespace, and (absent rapidfuzz) fuzzy search all miss. Without the block
    # fallback this edit is unlocatable, and _edit_contract raises 422.
    s = _Suggestion(
        "fees are payable within 30 days of the invoice date",
        "Fees are due within forty-five (45) days of invoice.",
    )
    [rec] = _anchor_suggestions(DOC, [s])
    assert rec["matched"] is True
    assert rec["applied"] is True
    assert rec["block_id"] is not None
    # Struck text is the real clause 2, verbatim — not the model's near-quote.
    assert rec["original_text"] == "2. Payment. Fees are due within thirty (30) days of invoice."
    assert DOC[rec["start"] : rec["end"]] == rec["original_text"]


def test_unrelated_quote_stays_unmatched():
    s = _Suggestion("governing law of the State of Delaware", "Governed by New York law.")
    [rec] = _anchor_suggestions(DOC, [s])
    assert rec["matched"] is False
    assert rec["applied"] is False


def test_exact_quote_still_precise_not_widened_to_block():
    # When the quote is exact, keep the tight span — don't coarsen to the block.
    s = _Suggestion("thirty (30) days", "forty-five (45) days")
    [rec] = _anchor_suggestions(DOC, [s])
    assert rec["matched"] is True
    assert rec["original_text"] == "thirty (30) days"
    assert rec["block_id"] is None
