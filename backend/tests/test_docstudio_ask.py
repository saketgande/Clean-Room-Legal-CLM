"""Asking a document: every citation checked before anyone sees it.

A citation is only worth showing if the words are really in the clause it
points at — that is what lets a reader click it and find them on the page. The
model is not trusted to have copied them right; these are the checks.
"""

from types import SimpleNamespace

from app.docstudio.ask import resolve

REGION = [{"page": 17, "bbox": {"x0": 0.1, "y0": 0.5, "x1": 0.9, "y1": 0.56}}]


def _clause(seq, text, label=None, page=None, regions=None, parent=None):
    return SimpleNamespace(
        seq=seq, clause_id=f"c{seq}", number_label=label, text=text, page_number=page,
        source_regions=regions, parent_clause_id=parent,
    )


CLAUSES = [
    _clause(1, "16.16 This Agreement shall be governed by the laws of the State of New York.", "16.16", 17, REGION),
    _clause(2, "16.17 If any term of this Agreement is invalid, the rest shall be unimpaired.", "16.17", 17),
    _clause(3, "IN WITNESS THEREOF, the parties have signed this Agreement."),
]


def test_words_found_in_the_cited_clause_are_verified_and_placed_on_its_page():
    [cite] = resolve([{"clause": 1, "quote": "the laws of the State of New York"}], CLAUSES)

    assert cite["verified"] is True
    assert (cite["n"], cite["clause_id"], cite["label"], cite["page"]) == (1, "c1", "16.16", 17)
    assert cite["regions"] == REGION  # what the viewer draws the box from


def test_words_cited_to_the_wrong_clause_are_moved_to_the_one_that_holds_them():
    """The common slip: right words, neighbouring clause number. Left alone,
    the click would light up a paragraph that does not say it."""
    [cite] = resolve([{"clause": 1, "quote": "the rest shall be unimpaired"}], CLAUSES)

    assert (cite["verified"], cite["clause_id"]) == (True, "c2")


def test_words_in_no_clause_are_shown_unverified_never_as_checked():
    """The failure that matters: an invented quote passed off as the contract's."""
    [cite] = resolve([{"clause": 2, "quote": "the laws of the State of Delaware"}], CLAUSES)

    assert cite["verified"] is False
    assert cite["clause_id"] == "c2"  # still says where the model pointed


def test_typography_and_markup_do_not_fail_a_real_quote():
    """A model straightens curly quotes and drops OCR markup when it copies;
    the words are still the contract's."""
    clauses = [_clause(7, "Fees for <b>“Time &amp; Material”</b> work are\npayable\u00a0monthly.")]

    [cite] = resolve([{"clause": 7, "quote": '"Time & Material" work are payable monthly'}], clauses)

    assert cite["verified"] is True


def test_an_unnumbered_clause_is_labelled_by_its_opening_words():
    [cite] = resolve([{"clause": 3, "quote": "the parties have signed"}], CLAUSES)

    assert cite["label"] == "IN WITNESS THEREOF, the parties…"


def test_a_citation_to_a_clause_that_does_not_exist_is_said_plainly():
    [cite] = resolve([{"clause": 99, "quote": "nothing like this"}], CLAUSES)

    assert (cite["verified"], cite["clause_id"], cite["label"]) == (False, None, "no such clause")


def test_a_paragraph_without_a_number_is_cited_as_the_clause_it_sits_in():
    """OCR'd contracts number the heading, not the paragraph under it; "16.16"
    is what a lawyer writes, not the paragraph's first five words."""
    clauses = [
        _clause(1, "16.16 Dispute Settlement", "16.16"),
        _clause(2, "This Agreement is governed by the laws of New York.", parent="c1"),
    ]

    [cite] = resolve([{"clause": 2, "quote": "the laws of New York"}], clauses)

    assert cite["label"] == "16.16"


def test_a_citation_written_as_a_line_is_read():
    [cite] = resolve(["#1 | the laws of the State of New York"], CLAUSES)

    assert (cite["verified"], cite["clause_id"]) == (True, "c1")


def _ask_with(citations, monkeypatch):
    import app.docstudio.ask as asking

    class Replies:
        name = "fake"

        def answer(self, document, question):
            return {"answer": "Governed by New York law [1].", "citations": citations}

    monkeypatch.setattr(asking, "clauses_for", lambda db, version_id: CLAUSES)
    return asking.ask(None, SimpleNamespace(id="v", org_id="o"), "Law?", answerer=Replies())


def test_a_list_sent_inside_the_models_own_call_syntax_is_still_read(monkeypatch):
    """The real reply, on a real contract: the list arrived as a string wrapped
    in '<parameter name="citations">'. Read naively, that string became one
    empty citation per character."""
    reply = _ask_with('\n<parameter name="citations">["#1 | the laws of the State of New York"]', monkeypatch)

    [cite] = reply["citations"]
    assert (cite["verified"], cite["clause_id"]) == (True, "c1")
    assert "warning" not in reply


def test_citations_with_no_list_in_them_show_none_and_say_why(monkeypatch):
    """Cut off after the tag, as another real reply was: nothing to recover,
    so nothing is shown — and the page says why instead of going quiet."""
    reply = _ask_with('\n<parameter name="clause">50', monkeypatch)

    assert reply["citations"] == []
    assert "unreadable" in reply["warning"]
