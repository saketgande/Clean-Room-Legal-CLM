"""Fill-in blanks in contract text ("[INSERT CORRECT EXPIRY DATE]", "[●]").

Round 2 (bug 3): the AI's fix for an impossible date wrote "[INSERT CORRECT EXPIRY DATE]"
into the contract, the automatic (paid) re-review flagged it, and the next fix carried
"[●]". Nothing stopped a contract with a blank from going out for signature. Three
guards share one detector (``app.contracts.blanks``):

* the AI's edit engine does not propose wording that holds a blank;
* an edit that still holds one cannot be accepted (older proposals are caught too);
* sending a contract for signature is refused while its text still has one.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.ai import tool_runtime as tr
from app.contract_files import routes
from app.contract_files.models import ContractEdit
from app.contract_files.schemas import ContractEditResponse
from app.contracts import blanks

# --- the detector -----------------------------------------------------------

REAL_BLANKS = [
    "[INSERT CORRECT EXPIRY DATE]",
    "[Insert date]",
    "[●]",
    "[•]",
    "[ ● ]",
    "[_____]",
    "[TBD]",
    "[TBC]",
    "[TO BE CONFIRMED: governing law]",
    "[to be agreed]",
    "[Date]",
    "[Company Name]",
    "[State/Country]",
    "[ENTER AMOUNT]",
    "[Specify notice period]",
    # the forms the AI actually wrote into the Northwind NDA (found live, round 3)
    "[Org's Home State/Country]",
    "[INSERT ORG HOME STATE/COUNTRY]",
    "[The Company]",
    "[Effective Date]",
    "[Counterparty Email]",
    # template blanks found in the live Statement of Work and NDA templates
    "[Role]",
    "[YYYY-MM-DD]",
    "[weekly / fortnightly]",
    "[List known exclusions, if any.]",
    "[Describe the business need and the outcome the Customer expects.]",
    "[Legal Contact Name/Title]",
]

NOT_BLANKS = [
    "[Intentionally omitted]",
    "[Signature page follows]",
    "[Remainder of page intentionally left blank]",
    "[sic]",
    "[Reserved]",
    "[x]",
    "[Addendum 1]",
    "[the Company]",
    "[a Party's]",
    "[2]",
    "[Exhibit A]",
    "[ ]",
    "[***]",
    "[...]",
    "[emphasis added]",
    "[t]he Parties",
    "see [2019] EWCA Civ 12",
    '(the "Company")',
    "Signed: ______________________",
    "The fee is USD 50,000 (fifty thousand).",
    "",
]


@pytest.mark.parametrize("blank", REAL_BLANKS)
def test_a_fill_in_blank_is_found(blank):
    assert blanks.find_blanks(f"This Agreement ends on {blank}, unless renewed.") == [blank]


@pytest.mark.parametrize("text", NOT_BLANKS)
def test_ordinary_bracketed_text_is_not_a_blank(text):
    assert blanks.find_blanks(f"Section 9. {text}") == []


def test_no_text_has_no_blanks():
    assert blanks.find_blanks(None) == []
    assert blanks.first_blank(None) is None


def test_each_blank_is_listed_once_in_the_order_it_appears_and_the_list_is_capped():
    text = "A [INSERT DATE]. B [●]. C [INSERT DATE]. D [TBD]."
    assert blanks.find_blanks(text) == ["[INSERT DATE]", "[●]", "[TBD]"]
    many = " ".join(f"[INSERT ITEM {i}]" for i in range(50))
    assert len(blanks.find_blanks(many)) == blanks.MAX_LISTED
    assert blanks.first_blank(text) == "[INSERT DATE]"


# --- the edit engine does not propose a blank --------------------------------


def _suggestion(replacement, original="Fees are due within thirty (30) days."):
    return SimpleNamespace(original_text=original, replacement_text=replacement, edit_type="replace",
                           rationale="r", risk_level="high", citations=[])


def test_a_suggestion_that_needs_a_fact_nobody_gave_is_left_out():
    keep = _suggestion("Fees are due within 45 days.")
    drop = _suggestion("This Agreement expires on [INSERT CORRECT EXPIRY DATE].")
    deletion = _suggestion("")
    kept, dropped = tr._without_blanks([keep, drop, deletion])
    assert kept == [keep, deletion] and dropped == ["[INSERT CORRECT EXPIRY DATE]"]


def test_deleting_text_is_not_a_blank():
    kept, dropped = tr._without_blanks([_suggestion(None), _suggestion("")])
    assert len(kept) == 2 and dropped == []


def test_the_refusal_names_what_a_person_has_to_supply():
    exc = tr._blank_refusal(["[INSERT CORRECT EXPIRY DATE]"])
    assert isinstance(exc, HTTPException) and exc.status_code == 422
    assert "[INSERT CORRECT EXPIRY DATE]" in exc.detail
    assert "type" in exc.detail.lower() or "supply" in exc.detail.lower()


def test_the_edit_prompt_tells_the_model_not_to_leave_blanks():
    from app.ai.prompt_versions import DEFAULT_SKILL_PROMPTS

    prompt = DEFAULT_SKILL_PROMPTS["contract_edit_suggestions"]
    assert "[INSERT" in prompt and "blank" in prompt.lower()


# --- an edit that holds a blank cannot be accepted -----------------------------


def _edit(replacement, *, edit_type="replace", start=10, end=20):
    return ContractEdit(
        id="e1", org_id="org-1", contract_id="c-1", contract_version_id="v-1", edit_type=edit_type,
        status="proposed", original_text="thirty days", replacement_text=replacement,
        citation=[{"type": "assistant_edit_version", "contract_version_id": "p-1"},
                  {"type": "anchor", "start": start, "end": end, "matched": True, "applied": True}],
    )


def test_the_page_is_told_which_blank_an_edit_would_write():
    out = ContractEditResponse.model_validate(_edit("expires on [INSERT CORRECT EXPIRY DATE]")).model_dump()
    assert out["blank"] == "[INSERT CORRECT EXPIRY DATE]"
    assert ContractEditResponse.model_validate(_edit("Fees are due in 45 days.")).model_dump()["blank"] is None


def test_a_persons_own_typed_edit_may_hold_a_blank():
    assert ContractEditResponse.model_validate(_edit("[Date]", edit_type="manual")).model_dump()["blank"] is None


def test_accepting_an_edit_that_holds_a_blank_is_refused_with_the_reason():
    problem = routes._accept_blocker(_edit("expires on [INSERT CORRECT EXPIRY DATE]"), [])
    assert problem and "[INSERT CORRECT EXPIRY DATE]" in problem and "reject" in problem.lower()
    assert routes._accept_blocker(_edit("Fees are due in 45 days."), []) is None


# --- sending for signature ---------------------------------------------------


def test_a_contract_with_a_blank_cannot_go_for_signature():
    from app.signatures.service import SignaturesService

    svc = SignaturesService.__new__(SignaturesService)
    with pytest.raises(HTTPException) as exc:
        svc.ensure_text_is_complete(text="Expires on [INSERT CORRECT EXPIRY DATE].", override=False)
    assert exc.value.status_code == 409
    assert "[INSERT CORRECT EXPIRY DATE]" in exc.value.detail
    svc.ensure_text_is_complete(text="Expires on 1 Jan 2027.", override=False)
    svc.ensure_text_is_complete(text="Expires on [INSERT CORRECT EXPIRY DATE].", override=True)
    svc.ensure_text_is_complete(text=None, override=False)
