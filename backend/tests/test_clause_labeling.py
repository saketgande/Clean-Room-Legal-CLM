"""Clause extraction labels the Documents reader's segments instead of re-finding them.

Failures this guards:
* the model being sent the whole contract and asked to copy clauses back — on
  the 2.4-4k-word drafting templates that ran past the output limit and stored
  no clauses at all, so drafted contracts had no clause index or risk score;
* a stored clause's text or offsets coming from the model instead of the
  segmentation (offsets the model "counts" were wrong 57% of the time);
* a label naming a segment that doesn't exist, or the same segment twice,
  becoming a stored clause;
* a section's span stopping at its heading instead of covering its sub-clauses.
"""

from types import SimpleNamespace as NS

from app.ai.clause_segments import EXCERPT_CHARS, clause_rows, segments_from_elements
from app.ai.prompt_builder import prompt_builder
from app.ai.prompt_versions import PromptBundle
from app.ai.registry import skill_registry
from app.ai.schemas import ClauseLabel

TEXT = (
    "MASTER SERVICES AGREEMENT\n\n"
    "13. LIMITATION OF LIABILITY\n"
    "13.1 Each Party's total liability is capped at the fees paid in the prior 12 months.\n"
    "13.2 Neither Party is liable for indirect or consequential loss.\n\n"
    "14. GENERAL\n"
    "14.1 Neither Party may assign this Agreement without the other's written consent.\n"
    "14.2 Notices must be in writing and sent to the addresses above.\n"
)


def _el(seq, kind, level, number, needle):
    start = TEXT.index(needle)
    line_end = TEXT.index("\n", start)
    return NS(seq=seq, element_type=kind, level=level, number_label=number,
              text=TEXT[start:line_end], char_start=start, char_end=line_end)


ELEMENTS = [
    _el(0, "paragraph", 1, None, "MASTER SERVICES"),
    _el(1, "clause", 1, "13.", "13. LIMITATION"),
    _el(2, "clause", 2, "13.1", "13.1 Each"),
    _el(3, "clause", 2, "13.2", "13.2 Neither"),
    _el(4, "clause", 1, "14.", "14. GENERAL"),
    _el(5, "clause", 2, "14.1", "14.1 Neither"),
    _el(6, "clause", 2, "14.2", "14.2 Notices"),
]


def _bundle(prompt: str) -> PromptBundle:
    return PromptBundle(prompt_key="k", version="1", prompt_hash="h", shared_system_prompt="sys",
                        skill_prompt=prompt, model_name="m", model_config_hash="c")


def _segments():
    return {s.number: s for s in segments_from_elements(ELEMENTS, TEXT)}


def test_a_section_spans_its_sub_clauses_and_a_sub_clause_only_itself():
    segs = _segments()
    sec = segs["13."]
    assert TEXT[sec.start:sec.end].startswith("13. LIMITATION")
    assert "13.2 Neither Party is liable for indirect" in TEXT[sec.start:sec.end]
    assert "14. GENERAL" not in TEXT[sec.start:sec.end]
    assert TEXT[segs["14.1"].start:segs["14.1"].end].startswith("14.1 Neither Party may assign")
    assert "14.2" not in TEXT[segs["14.1"].start:segs["14.1"].end]


def test_title_and_recital_paragraphs_are_not_offered():
    assert None not in _segments()  # the title paragraph has no number and isn't a clause


def test_the_model_sees_only_a_short_excerpt():
    assert all(len(s.excerpt) <= EXCERPT_CHARS for s in _segments().values())


def test_stored_clauses_take_the_segments_own_text_and_offsets():
    segs = _segments()
    labels = [ClauseLabel(segment_id=segs["13."].id, clause_type="limitation_of_liability", confidence="high"),
              ClauseLabel(segment_id=segs["14.1"].id, clause_type="assignment")]
    rows = clause_rows(list(segs.values()), labels, TEXT)
    assert [r["clause_type"] for r in rows] == ["limitation_of_liability", "assignment"]
    for r in rows:
        assert TEXT[r["start_char"]:r["end_char"]] == r["text"]
    assert rows[0]["heading"] == "13. LIMITATION OF LIABILITY"


def test_unknown_or_repeated_segment_ids_store_nothing():
    segs = _segments()
    labels = [ClauseLabel(segment_id="S99", clause_type="indemnification"),
              ClauseLabel(segment_id=segs["13."].id, clause_type="limitation_of_liability"),
              ClauseLabel(segment_id=segs["13."].id, clause_type="indemnification")]
    rows = clause_rows(list(segs.values()), labels, TEXT)
    assert [r["clause_type"] for r in rows] == ["limitation_of_liability"]


def test_labelling_does_not_send_the_full_contract_text():
    spec = skill_registry.get("clause_labeling")
    context = NS(manifest={"contract_id": "c1"}, text="FULL CONTRACT TEXT " * 500)
    built = prompt_builder.build_structured_skill_prompt(
        spec=spec, prompt_bundle=_bundle("label"),
        input_payload={"segments": [s.for_prompt() for s in _segments().values()]}, contract_context=context,
    )
    assert "FULL CONTRACT TEXT" not in built.user_prompt
    assert "13. LIMITATION OF LIABILITY" in built.user_prompt
    # The old extraction still gets the text it needs.
    old = prompt_builder.build_structured_skill_prompt(
        spec=skill_registry.get("clause_extraction"),
        prompt_bundle=_bundle("extract"),
        input_payload={}, contract_context=context,
    )
    assert "FULL CONTRACT TEXT" in old.user_prompt
