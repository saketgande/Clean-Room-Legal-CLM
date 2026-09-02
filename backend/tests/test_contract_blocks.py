from app.contract_files.blocks import (
    anchor_quote,
    block_by_id,
    split_blocks,
)

DOC = (
    "1. Term. This Agreement begins on the Effective Date and continues for one year.\n\n"
    "2. Payment. Fees are due within thirty (30) days of invoice.\n\n"
    "3. Confidentiality. Each party shall protect the other's Confidential Information."
)


def test_splits_on_blank_lines():
    assert [b.text[:2] for b in split_blocks(DOC)] == ["1.", "2.", "3."]


def test_ids_are_content_derived_so_editing_one_block_is_stable():
    before = split_blocks(DOC)
    after = split_blocks(DOC.replace("thirty (30)", "forty-five (45)"))
    assert after[0].id == before[0].id  # Term untouched
    assert after[2].id == before[2].id  # Confidentiality untouched
    assert after[1].id != before[1].id  # Payment changed


def test_splits_numbered_clauses_without_blank_lines():
    # OCR output rarely leaves blank lines between clauses. Splitting on blank
    # lines alone would merge these into one blob; clause-marker splitting keeps
    # them separate so a redline anchors to one clause, not the whole article.
    ocr = (
        '2.1 "Affiliate" means any entity under common control with a party.\n'
        '2.2 "Agreement" means this document together with its schedules.\n'
        '2.3 "Confidential Information" means non-public data of either party.'
    )
    blocks = split_blocks(ocr)
    assert len(blocks) == 3
    assert blocks[0].text.startswith("2.1")
    assert blocks[2].text.startswith("2.3")
    # a paraphrase of clause 2.1 anchors to 2.1 — not a merged super-block
    hit = anchor_quote(blocks, "affiliate is an entity under common control with a party")
    assert block_by_id(blocks, hit) == blocks[0]


def test_anchor_exact_and_fuzzy_and_refusal():
    blocks = split_blocks(DOC)
    # exact containment
    assert anchor_quote(blocks, "Fees are due within thirty (30) days") == blocks[1].id
    # paraphrase / whitespace + word drift still lands on the right clause
    assert (
        anchor_quote(blocks, "each   party  must  protect  Confidential Information")
        == blocks[2].id
    )
    # unrelated quote is refused, not misplaced
    assert anchor_quote(blocks, "governing law of the State of Delaware") is None


def test_generator_places_paraphrased_deviation_on_the_right_clause():
    # Reproduces the placement core of _create_playbook_redline_version: a model
    # quote that does NOT appear verbatim used to fail _find_phrase and get
    # appended at the end. Block anchoring must strike the real clause in place.
    source_text = DOC
    blocks = split_blocks(source_text)
    model_quote = "fees are payable within 30 days of the invoice date"  # paraphrased

    block = block_by_id(blocks, anchor_quote(blocks, model_quote))
    assert block is not None and block.text.startswith("2. Payment")

    idx = source_text.find(block.text)
    span = (idx, idx + len(block.text))
    assert idx >= 0  # exact, no fuzzy guessing needed

    # Build the proposal the way the generator does; the replacement lands where
    # clause 2 lives — not tacked onto the end of the document.
    replacement = "2. Payment. Fees are due within forty-five (45) days of invoice."
    proposal = source_text[: span[0]] + replacement + source_text[span[1] :]
    assert "forty-five (45)" in proposal
    assert proposal.index("forty-five") < proposal.index("Confidentiality")
    assert not proposal.rstrip().endswith(replacement)  # not appended at the end
