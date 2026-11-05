"""What the Data Processing Agreement's Annex 1 and Annex 3 are filled with from a request's answers (spec 008).

Pure functions, no database and no audit, shaped like ``intake.sow``: the six DPA fill-in fields
(``dpa_values``), the example reading the Templates page check and preview use, the facts a draft
records, and the strings the hold and the Processor refusal use. It imports only ``sow``,
``consultancy`` (its small text helpers) and settings, so ``intake.drafting`` and
``drafting_templates.service`` can import it without a cycle.

A real request is filled only from its own answers and an answer it lacks fills with nothing:
example content (``EXAMPLE_VALUES``) is returned only when a caller passes ``example=True`` (FR-34).
The standard sentences (the "only" limits, Section 6.1 authorisation, Section 9 conditions) are the
template's own wording, so they stay visible and editable there.
"""

from __future__ import annotations

from app.intake.consultancy import and_join, cell, rows_of, sentence

FIELDS = (
    "processing_purpose", "data_subject_categories", "personal_data_types",
    "special_category_data", "processing_locations", "authorised_subprocessors",
)

# The form's own options. "Several" / "Several of these" (older requests) are in neither: they say nothing
# about which, so they fill nothing.
SUBJECT_OPTIONS = ("Employees", "Customers", "Patients or trial subjects", "Suppliers")
SPECIAL_KIND = "Health or other special category"
_KIND_TEXT = {
    "Contact and identity": "names, contact details and identification data",
    "Employee / HR": "employment and HR records",
    SPECIAL_KIND: "health data and other special category data",
    "Financial": "financial and transaction data",
}

PROCESSOR_ROLE = "Processor: we act on their instructions"  # equal to the form's option; a test pins it
PROCESSOR_NOTE = (
    "No draft was made: this request says we are the Processor, and our DPA template is written for the "
    "Controller. Attach the other side's DPA (or the paper to use) before the review."
)
PROCESSOR_TITLE = "No draft made: the request says we are the Processor"
DPA_HELD = (
    "Enter these answers on the request first: {answers}. Annex 1 and Annex 3 of the DPA draft are made "
    "from them, and this request does not have them yet."
)
FREE_TEXT_HELD = (
    "File this request on the Data Processing Agreement form first. It came in by email or chat, so it "
    "has none of the form's answers, and Annex 1 and Annex 3 of the DPA draft are made from them: whose "
    "data it is, what kinds of data, where it is processed and which sub-processors are used."
)
SPECIAL_YES = (
    "Special category Personal Data is processed: health data and other special category data. The "
    "Processor shall process it only for the purpose stated in this Annex 1 and shall apply the measures "
    "in Annex 2 to it."
)
SPECIAL_NO = "None. No special category Personal Data is processed under this Agreement."


def held_message(labels: list[str]) -> str:
    """The 409 text naming the questions still to answer, each in double quotes, in form order."""
    return DPA_HELD.format(answers=and_join([f'"{label}"' for label in labels]))


# --- the six fields -------------------------------------------------------------------------------


def _picks(raw, known) -> list[str]:
    """The ticked options of a multi-tick answer, each once in the order stored. An older single choice
    is one tick; a choice that is not one of ``known`` (such as "Several") is none."""
    items = raw if isinstance(raw, list) else [raw]
    return [p for p in dict.fromkeys(cell(i) for i in items if isinstance(i, str)) if p in known]


def _countries(fv: dict) -> list[str]:
    """The countries outside India, only while the request says data leaves India."""
    if fv.get("transfer") != "Yes":
        return []
    return [c for c in (cell(r.get("country")) for r in rows_of(fv, "transfer_countries")) if c]


def _subprocessors(fv: dict) -> list[dict]:
    return rows_of(fv, "subprocessor_rows") if fv.get("subprocessors") == "Yes" else []


def dpa_values(fv: dict | None, *, example: bool = False) -> dict[str, str]:
    """The six DPA fill-in fields for a request's answers. A missing answer fills with nothing, so a
    request of another kind, or a held one, gets empty strings. ``example=True`` ignores the answers
    and returns the example reading (``EXAMPLE_VALUES``)."""
    if example:
        return dict(EXAMPLE_VALUES)
    fv = fv or {}
    subjects = _picks(fv.get("data_subjects"), SUBJECT_OPTIONS)
    kinds = _picks(fv.get("data_types"), _KIND_TEXT)
    countries = _countries(fv)
    if fv.get("transfer") == "No":
        locations = "India."
    else:
        locations = sentence(and_join(["India", *countries])) if countries else ""
    if fv.get("subprocessors") == "No":
        authorised = "None."
    else:
        authorised = "\n\n".join(
            f"{cell(r.get('name'))} ({cell(r.get('country'))}): {sentence(cell(r.get('processing')))}"
            for r in _subprocessors(fv)
        )
    return {
        "processing_purpose": str(fv.get("processing_purpose") or "").replace("\r\n", "\n").strip(),
        "data_subject_categories": "; ".join(subjects) + "." if subjects else "",
        "personal_data_types": "; ".join(_KIND_TEXT[k] for k in kinds) + "." if kinds else "",
        "special_category_data": (SPECIAL_YES if SPECIAL_KIND in kinds else SPECIAL_NO) if kinds else "",
        "processing_locations": locations,
        "authorised_subprocessors": authorised,
    }


def record_facts(fv: dict | None) -> dict:
    """What the draft was filled from, for its record (FR-49)."""
    fv = fv or {}
    return {
        "countries_outside_india": len(_countries(fv)),
        "subprocessors": len(_subprocessors(fv)),
        "special_category_data": SPECIAL_KIND in _picks(fv.get("data_types"), _KIND_TEXT),
    }


# --- the example reading --------------------------------------------------------------------------

# A complete example DPA request for the Templates page check and preview (FR-33): two kinds of people,
# two kinds of data including special category data, a country outside India and a sub-processor. It
# shares no purpose, country or sub-processor with any acceptance-test request, so finding it in a real
# draft means the example leaked (AC-38).
EXAMPLE_ANSWERS: dict = {
    "request_form": "dpa", "paper": "Our template", "our_role": "Controller: we decide how the data is used",
    "processing_purpose": "Running the occupational-health clinic booking system for staff and study volunteers.",
    "data_subjects": ["Employees", "Patients or trial subjects"],
    "data_types": ["Employee / HR", "Health or other special category"],
    "transfer": "Yes", "transfer_countries": [{"country": "Netherlands"}],
    "subprocessors": "Yes",
    "subprocessor_rows": [{"name": "Northwind Hosting Ltd", "country": "Netherlands",
                           "processing": "Hosting of the clinic booking database"}],
}
EXAMPLE_VALUES: dict[str, str] = dpa_values(EXAMPLE_ANSWERS)
# What marks example content. A check's suggested wording that still holds one of these (after the
# fields are turned back into codes) must never be applied to our paper (FR-34).
EXAMPLE_MARKERS: tuple[str, ...] = (
    EXAMPLE_ANSWERS["processing_purpose"], "Northwind Hosting Ltd",
    "Hosting of the clinic booking database", "Netherlands",
)
