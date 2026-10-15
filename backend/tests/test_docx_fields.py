"""Word field codes: found across runs, marked, filled, and the file left intact.

Failures this guards:
* Word stores ``{counterparty}`` as ``{`` + ``counter`` + ``party}`` in three runs, and a
  run-by-run reader never sees a code, so a draft goes out with the raw ``{counterparty}``;
* "Globex Corporation" typed twelve times, split across runs differently each time, marked
  once and filled in only some of the places;
* a filled value that loses the formatting around its place, or an empty value that leaves a
  double space, a blank paragraph, or deletes the paragraph that carries the section break;
* a code in a text box or footnote that nothing can fill, silently left in the draft;
* a rewritten package that Word has to "repair": styles, numbering, headers, page setup and
  media must come out byte for byte as they went in;
* an identical upload looking new because the zip was re-saved with different timestamps;
* a hostile .docx: an entity that reads a file off the server, or a zip that expands to
  gigabytes.

Fixtures are built here with python-docx (plus a little hand-written XML for what it can't
make: text boxes, footnotes, tracked changes); no binary file is committed. Every output is
re-opened with python-docx, which is the cheapest proof the package is still loadable.
"""

import io
import re
import types
import zipfile

import openpyxl
import pytest
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_BREAK
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches
from fastapi import HTTPException
from lxml import etree

from app.drafting_templates import docx_fields as dxf
from app.drafting_templates.service import (
    PLACEHOLDERS,
    SAMPLE_VALUES,
    TEMPLATES,
    check_body,
    default_body,
)

VALUES = {**SAMPLE_VALUES, "counterparty": "Acme Industries", "company": "Aegis Pharma Ltd"}
GLOBEX = "Globex Corporation"


# --- helpers -------------------------------------------------------------------------------


def _save(doc) -> bytes:
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _members(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {i.filename: archive.read(i) for i in archive.infolist()}


def _infos(data: bytes) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.infolist()


def _zip(members: dict[str, bytes], *, compress=zipfile.ZIP_DEFLATED, stamp=(2001, 2, 3, 4, 5, 6)):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, stamp)
            info.compress_type = compress
            archive.writestr(info, data)
    return out.getvalue()


def _reopen(data: bytes) -> Document:
    """The package must still be a loadable Word file."""
    return Document(io.BytesIO(data))


def _body_texts(data: bytes) -> list[str]:
    """Top-level paragraph texts as python-docx reads them: an oracle independent of the
    module under test."""
    return [p.text for p in _reopen(data).paragraphs]


def _cell_texts(data: bytes) -> list[str]:
    return [c.text for t in _reopen(data).tables for r in t.rows for c in r.cells]


def _runs(paragraph, *pieces):
    """Add runs one by one: each piece is (text, formatting) so a code can be split anywhere."""
    for text, fmt in pieces:
        run = paragraph.add_run(text)
        run.bold = fmt.get("bold")
        run.italic = fmt.get("italic")
        run.underline = fmt.get("underline")
    return paragraph


def _with_notes(data: bytes, *, footnote: str | None = None, endnote: str | None = None) -> bytes:
    """Add a footnotes and/or endnotes part (python-docx can't make them)."""
    members = _members(data)
    types_xml = members["[Content_Types].xml"].decode()
    rels_xml = members["word/_rels/document.xml.rels"].decode()
    for kind, text in (("footnotes", footnote), ("endnotes", endnote)):
        if text is None:
            continue
        one = kind[:-1]
        members[f"word/{kind}.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:{kind} {nsdecls("w")}><w:{one} w:id="1"><w:p><w:r><w:t>{text}</w:t></w:r></w:p>'
            f"</w:{one}></w:{kind}>"
        ).encode()
        types_xml = types_xml.replace(
            "</Types>",
            f'<Override PartName="/word/{kind}.xml" ContentType="application/vnd.openxmlformats-'
            f'officedocument.wordprocessingml.{kind}+xml"/></Types>',
        )
        rels_xml = rels_xml.replace(
            "</Relationships>",
            f'<Relationship Id="rIdNote{kind}" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/{kind}" Target="{kind}.xml"/></Relationships>',
        )
    members["[Content_Types].xml"] = types_xml.encode()
    members["word/_rels/document.xml.rels"] = rels_xml.encode()
    return _zip(members)


def _text_box(paragraph, text: str) -> None:
    paragraph._p.append(parse_xml(
        f'<w:r {nsdecls("w")} xmlns:v="urn:schemas-microsoft-com:vml"><w:pict><v:shape id="tb">'
        f"<v:textbox><w:txbxContent><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:txbxContent>"
        f"</v:textbox></v:shape></w:pict></w:r>"
    ))


def _template() -> bytes:
    """A realistic template: heading, numbered clause, a code split over three runs with
    different formatting, a table, a header, a footer, a section break and page setup."""
    doc = Document()
    doc.add_heading("Master Services Agreement", level=1)
    doc.add_paragraph("Effective on {effective_date}.", style="List Number")
    _runs(
        doc.add_paragraph(),
        ("This Agreement is made between {company} and ", {}),
        ("{", {"bold": True}),
        ("counter", {"italic": True}),
        ("party}", {"underline": True}),
        (". ", {}),
    )
    table = doc.add_table(rows=2, cols=2)
    table.style = "Table Grid"
    table.cell(0, 0).text = "Party"
    table.cell(0, 1).text = "{counterparty}"
    table.cell(1, 0).text = "Law"
    table.cell(1, 1).text = "Governed by {governing_law}."
    doc.add_paragraph("Fee: payable within {payment_days} days.{value_sentence}")
    doc.add_section(WD_SECTION.NEW_PAGE)
    doc.add_paragraph("Signature page for {counterparty}.")
    first = doc.sections[0]
    first.header.is_linked_to_previous = False
    first.header.paragraphs[0].text = "Draft for {company}"
    first.footer.is_linked_to_previous = False
    first.footer.paragraphs[0].text = "Confidential: {counterparty}"
    first.page_width = Inches(8.5)
    return _save(doc)


def _globex_doc() -> bytes:
    """"Globex Corporation" twelve times, each split across runs in a different place."""
    doc = Document()
    splits = [("Glo", "bex Corp", "oration"), ("Globex", " Corporation", ""),
              ("G", "lobex Corporati", "on"), ("Globex Cor", "poration", "")]
    for i in range(8):
        a, b, c = splits[i % 4]
        _runs(
            doc.add_paragraph(),
            (f"Clause {i}: the Supplier delivers to ", {}),
            (a, {"bold": True}),
            (b, {"italic": True}),
            (c, {"underline": True}),
            (" each month.", {}),
        )
    table = doc.add_table(rows=2, cols=2)
    for n in range(4):
        a, b, c = splits[n]
        _runs(table.cell(n // 2, n % 2).paragraphs[0], (a, {}), (b, {"bold": True}), (c, {}))
    return _save(doc)


def _occurrences(data: bytes, needle: str) -> list[dxf.Mark]:
    marks = []
    for para in dxf.paragraphs(data):
        at = para.text.find(needle)
        while at != -1:
            marks.append(dxf.Mark(para.id, at, at + len(needle), field="counterparty"))
            at = para.text.find(needle, at + len(needle))
    return marks


def _refused(call, *args) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        call(*args)
    assert caught.value.status_code == 422
    return caught.value


# --- finding and filling codes ---------------------------------------------------------------


def test_code_split_across_runs_is_found_filled_and_keeps_the_first_runs_formatting():
    """A run-by-run reader never sees ``{counterparty}`` when Word splits it into three
    runs, so the draft keeps the raw code. The value must also take the formatting of the
    run where the code starts, not of the last one."""
    template = _template()

    uses, problems = dxf.field_uses(template)

    assert problems == []
    counterparty = {u.place: u.count for u in uses if u.name == "counterparty"}
    assert counterparty == {"table": 1, "body": 2, "footer": 1}
    assert all(u.known and u.label for u in uses)

    out = dxf.fill(template, VALUES)

    doc = _reopen(out)
    para = next(p for p in doc.paragraphs if p.text.startswith("This Agreement is made"))
    assert para.text == "This Agreement is made between Aegis Pharma Ltd and Acme Industries. "
    holder = next(r for r in para.runs if "Acme" in r.text)
    assert holder.bold is True and not holder.italic and not holder.underline
    assert [r.text for r in para.runs if r.italic or r.underline] == ["", ""]
    assert holder._r.find(qn("w:t")).get(
        "{http://www.w3.org/XML/1998/namespace}space"
    ) == "preserve"


def test_filled_file_keeps_styles_numbering_tables_headers_footers_and_page_setup():
    """FR-41: a draft that comes out as a plain page, or with a numbered clause turned into
    ordinary text, is not the template the person uploaded."""
    template = _template()

    out = dxf.fill(template, VALUES)

    before, after = _reopen(template), _reopen(out)
    assert after.paragraphs[0].style.name == "Heading 1"
    assert after.paragraphs[1].text == "Effective on 2026-10-01."
    assert after.paragraphs[1].style.name == "List Number"
    assert after.tables[0].style.name == "Table Grid"
    assert _cell_texts(out) == ["Party", "Acme Industries", "Law",
                                "Governed by the State of Delaware."]
    assert after.sections[0].header.paragraphs[0].text == "Draft for Aegis Pharma Ltd"
    assert after.sections[0].footer.paragraphs[0].text == "Confidential: Acme Industries"
    assert after.sections[0].page_width == before.sections[0].page_width
    assert len(after.sections) == len(before.sections) == 2


def test_header_and_footer_are_filled():
    """A footer saying "Confidential: {counterparty}" is the most common place for a code
    and the easiest to forget, because it isn't in the document body."""
    out = dxf.fill(_template(), VALUES)

    members = _members(out)
    parts = {n: d.decode() for n, d in members.items() if n.startswith(("word/header", "word/footer"))}
    assert any("Draft for Aegis Pharma Ltd" in x for x in parts.values())
    assert any("Confidential: Acme Industries" in x for x in parts.values())
    assert not any("{" in x or "}" in x for x in parts.values())
    _reopen(out)


def test_a_code_split_over_a_hyperlink_and_a_content_control_is_found_and_filled():
    """Runs inside w:hyperlink and an inline w:sdt belong to the paragraph; reading only the
    direct runs of w:p loses the middle of the code."""
    doc = Document()
    para = doc.add_paragraph()
    para._p.append(parse_xml(f'<w:r {nsdecls("w")}><w:t>See {{counter</w:t></w:r>'))
    para._p.append(parse_xml(
        f'<w:hyperlink {nsdecls("w")}><w:r><w:rPr><w:b/></w:rPr><w:t>pa</w:t></w:r></w:hyperlink>'
    ))
    para._p.append(parse_xml(
        f'<w:sdt {nsdecls("w")}><w:sdtContent><w:r><w:t>rty}} now</w:t></w:r></w:sdtContent></w:sdt>'
    ))
    data = _save(doc)

    uses, problems = dxf.field_uses(data)
    out = dxf.fill(data, VALUES)

    assert problems == [] and [(u.name, u.count) for u in uses] == [("counterparty", 1)]
    # python-docx's own .text skips w:sdt content, so read the raw w:t nodes
    assert "".join(t.text or "" for t in _reopen(out).element.body.iter(qn("w:t"))) == (
        "See Acme Industries now"
    )


def test_literal_braces_are_kept_as_text_and_a_marked_literal_survives_filling():
    """``{{x}}`` is how a contract keeps a brace. If it were read as a code it would be
    refused; if filling forgot to unescape it the draft would show doubled braces."""
    doc = Document()
    doc.add_paragraph("Use {{braces}} for {counterparty}.")
    doc.add_paragraph("A clause in {curly} brackets.")
    data = _save(doc)

    _, problems = dxf.field_uses(data)
    assert [(p.kind, p.text, p.paragraph) for p in problems] == [("unknown_field", "{curly}", 1)]

    paras = dxf.paragraphs(data)
    start = paras[1].text.index("{curly}")
    fixed = dxf.apply_marks(data, [dxf.Mark(1, start, start + 7, literal=True)])
    assert dxf.paragraphs(fixed)[1].text == "A clause in {{curly}} brackets."
    assert dxf.field_uses(fixed)[1] == []

    assert _body_texts(dxf.fill(fixed, VALUES)) == [
        "Use {braces} for Acme Industries.", "A clause in {curly} brackets."
    ]


def test_unknown_and_unpaired_codes_are_reported_with_the_documented_words():
    """FR-34: a code drafting can't fill must be named, with where it is, before it can
    reach a draft. The wording is pinned because the page shows it as is."""
    doc = Document()
    doc.add_paragraph("Fine {company}.")
    doc.add_paragraph("Hello {client_name} there")
    doc.add_paragraph("Unclosed {company")
    doc.add_paragraph("Lone } brace")
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "{0}"
    data = _save(doc)

    uses, problems = dxf.field_uses(data)

    assert [(u.name, u.place) for u in uses] == [("0", "table"), ("client_name", "body"),
                                                 ("company", "body")]
    assert [u.known for u in uses] == [False, False, True]
    assert [(p.kind, p.paragraph, p.start, p.end, p.text, p.place) for p in problems] == [
        ("unknown_field", 1, 6, 19, "{client_name}", "body"),
        ("unpaired_brace", 2, 9, 10, "{", "body"),
        ("unpaired_brace", 3, 5, 6, "}", "body"),
        ("unknown_field", 4, 0, 3, "{0}", "table"),
    ]
    assert problems[0].message == (
        "Unknown placeholder {client_name} (in the body). Use one from the list, "
        "or keep it as ordinary text by writing it as {{client_name}}."
    )
    assert problems[1].message == (
        "The braces don't pair up (in the body). Write a literal brace as {{ or }}."
    )
    assert problems[3].message.startswith("Unknown placeholder {0} (in a table).")
    exc = _refused(dxf.fill, data, VALUES)
    assert exc.detail == problems[0].message


def test_codes_in_a_text_box_footnote_or_endnote_are_problems_not_uses():
    """Nothing fills a text box or a footnote, so a code there would survive into the draft
    as a raw ``{counterparty}``. It must block confirmation instead (FR-34)."""
    doc = Document()
    anchor = doc.add_paragraph("Anchor {counterparty}")
    _text_box(anchor, "Hidden {company}")
    data = _with_notes(_save(doc), footnote="Note {counterparty}", endnote="End {company}")

    uses, problems = dxf.field_uses(data)
    paras = dxf.paragraphs(data)

    assert [(u.name, u.place) for u in uses] == [("counterparty", "body")]
    assert [(p.kind, p.place, p.text) for p in problems] == [
        ("unfillable_place", "text_box", "{company}"),
        ("unfillable_place", "footnote", "{counterparty}"),
        ("unfillable_place", "endnote", "{company}"),
    ]
    assert [p.message for p in problems] == [
        "Text in a text box can't be filled; keep it as ordinary text.",
        "Text in a footnote can't be filled; keep it as ordinary text.",
        "Text in an endnote can't be filled; keep it as ordinary text.",
    ]
    assert paras[0].text == "Anchor {counterparty}"  # the box's text isn't the anchor's
    assert [(p.place, p.fillable) for p in paras] == [
        ("body", True), ("text_box", False), ("footnote", False), ("endnote", False)
    ]
    _refused(dxf.fill, data, VALUES)


def test_a_literal_mark_keeps_a_brace_in_a_text_box_but_a_field_mark_there_is_refused():
    """FR-34 lets a person "keep the text as ordinary text" for a code in a place that can't
    be filled. A field mark there would be unfillable; a literal one resolves the problem."""
    doc = Document()
    anchor = doc.add_paragraph("Anchor")
    _text_box(anchor, "See {x}")
    data = _save(doc)
    box = next(p for p in dxf.paragraphs(data) if p.place == "text_box")

    _refused(dxf.apply_marks, data, [dxf.Mark(box.id, 4, 7, field="company")])
    fixed = dxf.apply_marks(data, [dxf.Mark(box.id, 4, 7, literal=True)])

    assert dxf.field_uses(fixed)[1] == []
    filled = dxf.fill(fixed, VALUES)
    assert next(p for p in dxf.paragraphs(filled) if p.place == "text_box").text == "See {x}"


# --- marking ---------------------------------------------------------------------------------


def test_marking_globex_corporation_twelve_times_across_runs_marks_every_one():
    """AC-30: "Globex Corporation" is stored as different run splits in each place. Marking
    must work on the paragraph text, so all twelve become the field and none is missed."""
    data = _globex_doc()
    marks = _occurrences(data, GLOBEX)
    assert len(marks) == 12

    marked = dxf.apply_marks(data, marks)

    uses, problems = dxf.field_uses(marked)
    assert problems == []
    assert [(u.name, u.place, u.count) for u in uses] == [("counterparty", "body", 8),
                                                          ("counterparty", "table", 4)]
    assert GLOBEX not in "".join(p.text for p in dxf.paragraphs(marked))
    out = dxf.fill(marked, VALUES)
    assert "".join(_body_texts(out) + _cell_texts(out)).count("Acme Industries") == 12
    assert GLOBEX not in "".join(_body_texts(out) + _cell_texts(out))
    assert _body_texts(out)[3] == "Clause 3: the Supplier delivers to Acme Industries each month."
    # the marked passage took the first run's formatting: bold, in a bold/italic/underline split
    run = next(r for r in _reopen(marked).paragraphs[0].runs if r.text == "{counterparty}")
    assert run.bold is True


def test_marking_accepts_any_object_with_the_mark_fields():
    """Routes pass validated request models, not this module's dataclass."""
    data = _globex_doc()
    para = dxf.paragraphs(data)[0]
    at = para.text.index(GLOBEX)
    mark = types.SimpleNamespace(paragraph=0, start=at, end=at + len(GLOBEX),
                                 field="counterparty", literal=False)

    marked = dxf.apply_marks(data, [mark, mark])  # the same mark twice is one mark

    assert dxf.field_uses(marked)[0][0].count == 1


def test_a_mark_across_a_tab_or_line_break_is_refused_and_tab_stops_are_not_tabs():
    """A tab has no text node, so a mark spanning one would drop it silently. A tab *stop*
    in the paragraph properties is not a tab character and must not split the text."""
    doc = Document()
    para = doc.add_paragraph()
    para.paragraph_format.tab_stops.add_tab_stop(Inches(2))
    run = para.add_run("Total")
    run.add_tab()
    run.add_text("$500")
    run.add_break()
    run.add_text("end")
    data = _save(doc)

    assert dxf.paragraphs(data)[0].text == "Total\t$500\nend"
    message = "A field can only mark text within one paragraph, not across a tab or line break."
    assert _refused(dxf.apply_marks, data, [dxf.Mark(0, 3, 7, field="company")]).detail == message
    assert _refused(dxf.apply_marks, data, [dxf.Mark(0, 7, 11, literal=True)]).detail == message
    marked = dxf.apply_marks(data, [dxf.Mark(0, 6, 10, field="payment_days")])
    assert dxf.paragraphs(marked)[0].text == "Total\t{payment_days}\nend"


@pytest.mark.parametrize(
    "mark",
    [
        dxf.Mark(7, 0, 1, field="company"),
        dxf.Mark(0, 0, 999, field="company"),
        dxf.Mark(0, 5, 5, field="company"),
        dxf.Mark(0, -1, 2, field="company"),
    ],
)
def test_a_mark_outside_its_paragraph_is_refused_with_the_paragraph_number(mark):
    """A mark with a stale or invented offset would cut the wrong text; it must be 422."""
    doc = Document()
    doc.add_paragraph("Hello world")

    exc = _refused(dxf.apply_marks, _save(doc), [mark])

    assert exc.detail == f"That mark points outside the text of paragraph {mark.paragraph}."


def test_unknown_field_and_overlapping_marks_are_refused():
    """A mark naming a field drafting doesn't fill would produce a template that can't be
    filled; two overlapping marks would rewrite the same text twice."""
    doc = Document()
    doc.add_paragraph("Hello world today")
    data = _save(doc)

    assert _refused(dxf.apply_marks, data, [dxf.Mark(0, 0, 5, field="nope")]).detail.startswith(
        "Unknown placeholder {nope}"
    )
    overlap = _refused(
        dxf.apply_marks, data,
        [dxf.Mark(0, 0, 8, field="company"), dxf.Mark(0, 6, 11, field="counterparty")],
    )
    assert overlap.detail == "Two marks overlap in paragraph 0."


def test_no_marks_returns_the_same_bytes():
    """An empty mark list must not re-zip: an unchanged upload has to stay unchanged."""
    data = _template()

    assert dxf.apply_marks(data, []) is data


# --- empty values ----------------------------------------------------------------------------


def test_an_empty_value_leaves_no_blank_paragraph_double_space_or_stray_space():
    """FR-42: a field with no value must not leave a gap. The cases are the ones Word
    contracts have: mid-sentence, between two spaces, at the end, before a full stop, and a
    paragraph that was only the code."""
    doc = Document()
    doc.add_paragraph("")
    doc.add_paragraph("Total fee{value_sentence} payable monthly.")
    doc.add_paragraph("Hello {nda_kind_title} world")
    doc.add_paragraph("{value_sentence}")
    doc.add_paragraph("Pay now {value_sentence}")
    doc.add_paragraph("See {value_sentence}.")
    doc.add_paragraph("{nda_kind_title} {value_sentence} Kept text")
    data = _save(doc)
    values = {**VALUES, "value_sentence": "", "nda_kind_title": ""}

    out = dxf.fill(data, values)

    assert _body_texts(out) == [
        "",  # a blank line the template already had is not ours to remove
        "Total fee payable monthly.",
        "Hello world",
        "Pay now",
        "See.",
        "Kept text",
    ]


def test_the_section_break_paragraph_is_kept_when_its_only_content_is_an_empty_code():
    """Removing the paragraph that carries w:sectPr silently merges two sections and drops
    the page setup of the first."""
    doc = Document()
    doc.add_paragraph("Before")
    doc.add_section(WD_SECTION.NEW_PAGE)
    doc.paragraphs[-1].add_run("{value_sentence}")
    doc.add_paragraph("After")
    data = _save(doc)

    out = dxf.fill(data, {**VALUES, "value_sentence": ""})

    reopened = _reopen(out)
    assert len(reopened.sections) == 2
    assert len(reopened.element.body.xpath("//w:p/w:pPr/w:sectPr")) == 1
    assert [p.text for p in reopened.paragraphs] == ["Before", "", "After"]


def test_a_cells_last_paragraph_page_breaks_and_fields_survive_an_empty_code():
    """A cell must end in a paragraph or Word reports the file as corrupt; a page break is
    content even when there is no text beside it."""
    doc = Document()
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "{value_sentence}"
    cell = table.cell(0, 1)
    cell.paragraphs[0].text = "{value_sentence}"
    cell.add_paragraph("Second")
    cell.add_paragraph("{value_sentence}")
    broken = doc.add_paragraph("{value_sentence}")
    broken.add_run().add_break(WD_BREAK.PAGE)
    data = _save(doc)

    out = dxf.fill(data, {**VALUES, "value_sentence": ""})

    reopened = _reopen(out)
    left, right = reopened.tables[0].rows[0].cells
    assert [p.text for p in left.paragraphs] == [""]  # the only paragraph stays
    assert [p.text for p in right.paragraphs] == ["Second"]  # blank ones go: it still ends in a p
    assert len(reopened.element.body.xpath("//w:br[@w:type='page']")) == 1
    assert len(reopened.paragraphs) == 1


def test_values_with_quotes_braces_markup_and_non_english_text_are_inserted_as_plain_text():
    """FR-42 edge: a counterparty called ``O'Brien & "Sons" <Ltd> {x}`` must not break the XML
    or be read as a code, and a control character must not make lxml refuse the draft."""
    doc = Document()
    doc.add_paragraph("Between {company} and {counterparty}.")
    data = _save(doc)
    values = {
        **VALUES,
        "company": "O'Brien & \"Sons\" <Ltd> {x} {{y}}",
        "counterparty": "株式会社 Ünï 🎉 first\r\nsecond\x00\x0b\tthird" + "A" * 3000,
    }

    out = dxf.fill(data, values)

    assert _body_texts(out) == [
        "Between O'Brien & \"Sons\" <Ltd> {x} {{y}} and "
        "株式会社 Ünï 🎉 first second third" + "A" * 3000 + "."
    ]


def test_filling_twice_gives_identical_bytes_and_a_file_without_codes_is_returned_as_is():
    """Drafting must be reproducible (the same request twice is the same file), and a file
    with nothing to fill must not be re-zipped."""
    data = _template()

    assert dxf.fill(data, VALUES) == dxf.fill(data, VALUES)
    plain = _save(Document())
    assert dxf.fill(plain, VALUES) is plain


def test_filling_refuses_a_code_it_has_no_value_for():
    """A missing value left as ``{x}`` would reach the contract; it must stop the draft."""
    doc = Document()
    doc.add_paragraph("Hello {company}")

    exc = _refused(dxf.fill, _save(doc), {"counterparty": "x"})

    assert exc.detail.startswith("Unknown placeholder {company}")


# --- agreement with the text path ------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "Between {company} and {counterparty}.", "{client_name}", "{{literal}} {company}",
        "{", "}", "a { b", "{company", "{}", "{0}", "{{{company}}}", "{company!r}",
        "{company:>10}", "{company.x}", "{company!}", "}}}", "{{{", "{ company }",
        "{company:{counterparty}}",
    ],
)
def test_a_word_paragraph_is_judged_exactly_like_the_text_template(body):
    """FR-32/34: the same wording must be accepted or refused whether it came from a text box
    on the page or a Word file, and filled identically. A tokenizer that differs from
    ``service.check_body`` would let a template through that then breaks drafting."""
    doc = Document()
    doc.add_paragraph(body)
    data = _save(doc)

    problems = dxf.field_uses(data)[1]

    try:
        check_body(body)
        text_ok = True
    except HTTPException:
        text_ok = False
    assert (problems == []) is text_ok
    if text_ok:
        assert _body_texts(dxf.fill(data, SAMPLE_VALUES)) == [body.format(**SAMPLE_VALUES)]


@pytest.mark.parametrize("code", ["{company!r}", "{company:>10}"])
def test_a_conversion_or_format_spec_is_an_unknown_field_in_word(code):
    """A field is a bare ``{name}``: ``str.format`` would accept these but put quotes or padding
    into the contract, and the page already flags them. Neither reading the file nor filling it
    may treat them as the ``company`` field."""
    doc = Document()
    doc.add_paragraph(f"Between {code} and {{counterparty}}.")
    data = _save(doc)

    uses, problems = dxf.field_uses(data)

    assert [(p.kind, p.text) for p in problems] == [("unknown_field", code)]
    assert problems[0].message == (
        f"Unknown placeholder {code} (in the body). Use one from the list, or keep it as "
        f"ordinary text by writing it as {code.replace('{', '{{').replace('}', '}}')}."
    )
    assert "company" not in {u.name for u in uses}
    assert _refused(dxf.fill, data, VALUES).detail == problems[0].message


@pytest.mark.parametrize("key", sorted(TEMPLATES))
def test_every_shipped_template_fills_in_word_exactly_as_it_does_as_text(key):
    """The shipped wordings are the real corpus of codes. Word must accept all of them and
    produce the very text ``str.format`` does, or a Word-based copy of a shipped template
    would draft differently from the original.

    ``fill`` puts a value on one line, so the expected text is built from values with their
    line breaks collapsed. That keeps this test proving the WORDING is identical on both paths.
    It also means a multi-line value (the Statement of Work lists from spec 007) fills as ONE
    Word paragraph today, not one paragraph per entry. 006 Phase 3 (T023) must split such
    values into separate paragraphs before Word-based drafts ship."""
    body = default_body(key)
    doc = Document()
    for line in body.split("\n"):
        doc.add_paragraph(line)
    data = _save(doc)

    uses, problems = dxf.field_uses(data)

    assert problems == []
    assert {u.name for u in uses} <= {p["name"] for p in PLACEHOLDERS}
    one_line = {name: re.sub(r"[\r\n\t]+", " ", value) for name, value in SAMPLE_VALUES.items()}
    assert _body_texts(dxf.fill(data, SAMPLE_VALUES)) == body.format(**one_line).split("\n")


# --- paragraphs ------------------------------------------------------------------------------


def test_paragraph_ids_are_stable_ordered_and_name_their_place():
    """Marks refer to paragraph ids, so the ids must be the same for the same bytes and must
    order body, headers, footers, footnotes, endnotes."""
    data = _with_notes(_template(), footnote="F", endnote="E")

    first, second = dxf.paragraphs(data), dxf.paragraphs(data)

    assert first == second
    assert [p.id for p in first] == list(range(len(first)))
    places = [p.place for p in first]
    assert places[:3] == ["body", "body", "body"] and "table" in places
    order = [places.index(x) for x in ("header", "footer", "footnote", "endnote")]
    assert order == sorted(order) and places[-2:] == ["footnote", "endnote"]
    assert first[1].text == "Effective on {effective_date}."
    assert all(p.fillable for p in first if p.place in ("body", "table", "header", "footer"))


# --- tracked changes and comments ------------------------------------------------------------


def _tracked(doc) -> None:
    para = doc.add_paragraph("Text ")
    para._p.append(parse_xml(
        f'<w:ins {nsdecls("w")} w:id="9" w:author="A" w:date="2026-01-01T00:00:00Z">'
        f"<w:r><w:t>added</w:t></w:r></w:ins>"
    ))


def test_tracked_changes_are_refused_with_what_to_clear():
    """FR-29: with a tracked deletion, "is this sentence in the contract?" has no answer, so
    the file must be refused and the person told how to resolve it."""
    doc = Document()
    _tracked(doc)

    exc = _refused(dxf.refuse_revisions_and_comments, _save(doc))

    assert exc.detail == (
        "This Word file has tracked changes. Accept or reject every change "
        "(Review → Accept/Reject All), save, and upload it again."
    )


def test_a_tracked_change_in_a_footer_is_refused_too():
    """A revision in a header or footer part is as invisible to a body-only check as it is
    to the person who uploads the file."""
    doc = Document()
    doc.add_paragraph("Clean body")
    doc.sections[0].footer.is_linked_to_previous = False
    footer = doc.sections[0].footer.paragraphs[0]
    footer._p.append(parse_xml(
        f'<w:del {nsdecls("w")} w:id="3" w:author="A" w:date="2026-01-01T00:00:00Z">'
        f"<w:r><w:delText>old</w:delText></w:r></w:del>"
    ))

    exc = _refused(dxf.refuse_revisions_and_comments, _save(doc))

    assert "tracked changes" in exc.detail and "comments" not in exc.detail


def test_comments_are_refused_with_what_to_clear():
    """FR-29: a comment is someone's private note and would travel into every draft."""
    doc = Document()
    run = doc.add_paragraph().add_run("Commented text")
    doc.add_comment(run, text="Ask legal", author="Alice")

    exc = _refused(dxf.refuse_revisions_and_comments, _save(doc))

    assert exc.detail == (
        "This Word file has comments. Delete them (Review → Delete All Comments), "
        "save, and upload it again."
    )


def test_tracked_changes_and_comments_together_give_both_messages():
    """The person should clear both in one go, not find the second one on the next upload."""
    doc = Document()
    _tracked(doc)
    doc.add_comment(doc.paragraphs[0].runs[0], text="Ask legal", author="Alice")

    exc = _refused(dxf.refuse_revisions_and_comments, _save(doc))

    assert "tracked changes" in exc.detail and "has comments" in exc.detail


def test_a_clean_file_is_not_refused():
    """The check must not cry wolf: paragraph-property tab stops, styles and tables are not
    revisions."""
    assert dxf.refuse_revisions_and_comments(_template()) is None


# --- hashing and the zip ---------------------------------------------------------------------


def test_content_hash_is_stable_across_rezipping_and_changes_with_content():
    """FR-37: re-saving identical content (new timestamps, another compression, a different
    member order) must not look like a new upload, while a one-letter edit must."""
    data = _template()
    members = _members(data)
    reversed_order = dict(reversed(list(members.items())))

    same = {
        dxf.content_hash(data),
        dxf.content_hash(_zip(members, stamp=(2020, 12, 31, 23, 59, 58))),
        dxf.content_hash(_zip(members, compress=zipfile.ZIP_STORED)),
        dxf.content_hash(_zip(reversed_order)),
    }
    members["word/document.xml"] = members["word/document.xml"].replace(b"Effective", b"Effectivd")

    assert len(same) == 1
    assert dxf.content_hash(_zip(members)) not in same


def test_parts_other_than_the_changed_ones_come_out_byte_identical_in_the_same_order():
    """FR-41: styles, numbering, settings, page setup, media and unknown parts must be copied,
    not re-serialised, or Word may "repair" the file and drop formatting."""
    data = _template()
    members = _members(data)
    members["customXml/item1.xml"] = b"<root>kept as is</root>"
    members["word/media/image1.png"] = bytes(range(256)) * 8
    data = _zip(members, stamp=(2019, 5, 6, 7, 8, 10))

    for out in (dxf.fill(data, VALUES), dxf.apply_marks(data, [dxf.Mark(2, 0, 4, field="company")])):
        after = _members(out)
        changed = {n for n in members if after[n] != members[n]}
        assert "word/document.xml" in changed
        assert changed <= {"word/document.xml", "word/header1.xml", "word/footer1.xml"}
        assert {"word/styles.xml", "word/numbering.xml", "word/settings.xml",
                "customXml/item1.xml", "word/media/image1.png", "[Content_Types].xml"} \
            <= set(members) - changed
        assert [i.filename for i in _infos(out)] == [i.filename for i in _infos(data)]
        assert [(i.date_time, i.compress_type) for i in _infos(out)] == [
            (i.date_time, i.compress_type) for i in _infos(data)
        ]
        _reopen(out)


def test_xxe_payload_is_not_resolved():
    """An entity that reads a file off the server and puts it into the template text would
    leak it to whoever uploads: it must be refused, and its content must appear nowhere."""
    members = _members(_template())
    secret = "TOP-SECRET-FROM-THE-SERVER-DISK"
    members["word/document.xml"] = (
        '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY xxe SYSTEM "file:///etc/hostname">'
        '<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        f'<w:document {nsdecls("w")}><w:body><w:p><w:r><w:t>&xxe;&lol2;</w:t></w:r></w:p>'
        f"</w:body></w:document>"
    ).encode()
    evil = _zip(members)

    for call in (dxf.paragraphs, dxf.field_uses, dxf.refuse_revisions_and_comments,
                 lambda d: dxf.fill(d, VALUES), lambda d: dxf.apply_marks(d, [])):
        exc = _refused(call, evil)
        assert exc.detail == "This file is damaged and can't be read."
        assert secret not in exc.detail


def test_the_parser_leaves_entities_unresolved_even_without_the_doctype_refusal(tmp_path):
    """Second line of defence: if a DOCTYPE ever got past the refusal, the parser itself must
    still not read the file the entity points at (a naive parser does, shown as the control)."""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-FROM-THE-SERVER-DISK")
    xml = (
        f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY xxe SYSTEM "file://{secret}">]>'
        f'<w:document {nsdecls("w")}><w:body><w:p><w:r><w:t>a&xxe;b</w:t></w:r></w:p></w:body>'
        f"</w:document>"
    ).encode()
    naive = etree.fromstring(xml, etree.XMLParser(resolve_entities=True, load_dtd=True))
    safe = etree.fromstring(xml, dxf._parser())

    assert "TOP-SECRET" in "".join(naive.itertext())
    assert "TOP-SECRET" not in "".join(safe.itertext())
    assert "TOP-SECRET" not in etree.tostring(safe).decode()


def test_a_zip_bomb_is_refused_before_anything_is_read():
    """A 8 MB member of zeros is a few KB compressed: the Documents reader's expansion limit
    must stop it for every entry point, not only for contract uploads."""
    members = _members(_template())
    members["word/bomb.bin"] = b"\0" * (8 * 1024 * 1024)
    bomb = _zip(members)
    assert len(bomb) < 100_000

    for call in (dxf.paragraphs, dxf.field_uses, dxf.refuse_revisions_and_comments,
                 dxf.content_hash, lambda d: dxf.fill(d, VALUES),
                 lambda d: dxf.apply_marks(d, [dxf.Mark(0, 0, 1, literal=True)])):
        assert "can't be opened safely" in _refused(call, bomb).detail


def test_files_that_are_not_word_documents_are_refused_as_damaged():
    """Not a zip, a truncated zip, and a zip that is another Office format (a renamed
    spreadsheet) all mean "can't be read", not a 500."""
    sheet = io.BytesIO()
    openpyxl.Workbook().save(sheet)
    template = _template()

    for bad in (b"not a zip at all", template[: len(template) // 2], b""):
        for call in (dxf.paragraphs, dxf.content_hash):
            assert _refused(call, bad).detail == "This file is damaged and can't be read."
    assert _refused(dxf.paragraphs, sheet.getvalue()).detail == (
        "This file is damaged and can't be read."
    )
