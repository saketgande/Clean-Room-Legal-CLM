from app.contract_files.blocks import block_id_for
from app.contract_files.structure import build_elements, elements_from_flat_text, relevance


def test_relevance_keys_on_substance_not_edit_verbs():
    liability = "Limitation of Liability. In no event shall either party's aggregate liability exceed the twelve-month fee cap; no consequential damages."
    payment = "Fees are due within thirty days of invoice; late payments accrue interest."
    q = "reduce the limitation of liability cap"
    # the liability clause clearly wins, and clears the confidence floor
    assert relevance(q, liability) > relevance(q, payment)
    assert relevance(q, liability) >= 0.34
    # a vague instruction keys on nothing → low score → caller falls back to full text
    assert relevance("make it better and cleaner", liability) < 0.34

RAW = [
    {"type": "title", "content": "MASTER SERVICES AGREEMENT", "confidence": 0.99, "page_id": 0},
    {"type": "page_header", "content": "Lee County / IT Outsourced Services", "confidence": 0.8, "page_id": 1},
    {"type": "section_header", "content": "Article 2", "confidence": 0.96, "page_id": 1},
    {"type": "text", "content": '2.1 "Affiliate" means an entity under common control.', "confidence": 0.94, "page_id": 1},
    {"type": "table", "content": "Year Fees 1 100000", "html": "<table><tr><td>1</td></tr></table>", "confidence": 0.9, "page_id": 2},
]
# The snapshot text a provider would store: its elements joined in order.
TARGET = "\n".join(e["content"] for e in RAW)


def test_types_are_normalized_and_page_junk_is_flagged():
    els, ok = build_elements(RAW, TARGET)
    assert ok
    kinds = [e["element_type"] for e in els]
    assert kinds == ["title", "page_artifact", "heading", "clause", "table"]
    assert els[3]["number_label"] == "2.1"   # paragraph + clause number → "clause"
    assert els[2]["number_label"] == "Article 2"
    assert any(e["element_type"] == "page_artifact" for e in els)  # header is filterable


def test_table_html_and_confidence_and_page_are_kept():
    els, _ = build_elements(RAW, TARGET)
    table = els[4]
    assert table["html"].startswith("<table>")
    assert table["confidence"] == 0.9
    assert table["page_number"] == 2
    assert els[3]["confidence"] == 0.94


def test_offsets_index_into_the_unchanged_snapshot_text():
    # THE invariant: an element's [char_start, char_end] slices the ACTUAL
    # snapshot text back to that element verbatim — without altering the text.
    els, ok = build_elements(RAW, TARGET)
    assert ok
    for e in els:
        assert TARGET[e["char_start"]:e["char_end"]] == e["text"]


def test_unlocatable_element_reports_failure_so_caller_can_degrade():
    els, ok = build_elements(
        [{"type": "text", "content": "a clause that is not present in the target"}],
        TARGET,
    )
    assert ok is False  # signals: skip structuring, stay flat_only


def test_block_id_matches_the_splitter_scheme():
    els, _ = build_elements(RAW, TARGET)
    assert els[3]["block_id"] == block_id_for(els[3]["text"])


def test_flat_text_fallback_splits_into_clauses():
    doc = (
        '2.1 "Affiliate" means an entity under common control.\n'
        '2.2 "Agreement" means this document and its schedules.'
    )
    els, ok = elements_from_flat_text(doc)
    assert ok and len(els) == 2
    assert all(e["source"] == "from_flat_text" for e in els)
    for e in els:
        assert doc[e["char_start"]:e["char_end"]] == e["text"]
