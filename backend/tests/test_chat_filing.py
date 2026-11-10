"""The chat must ask before it files. A request filed on the first message, labelled
"Contract Review" with no vendor, value or date, is what these tests guard against."""

from datetime import date, timedelta

from app.intake.agents import BUILTIN_EXTRA_TYPES
from app.intake.chat_filing import canonical_label, details_block, refusal

FULL = dict(type_label="Other", subject="Diwali headsets", department="Research",
            description="Buy 100 headsets as Diwali gifts for the research team.",
            counterparty="Boat Lifestyle", estimated_value="INR 3,00,000", needed_by=(date.today() + timedelta(days=90)).isoformat())  # a fixed date goes stale: filing refuses past dates


def test_invented_label_is_refused_and_lists_the_real_ones():
    msg = refusal(**{**FULL, "type_label": "Contract Review"})
    assert all(t in msg for t in BUILTIN_EXTRA_TYPES)


def test_label_is_matched_without_regard_to_case():
    assert canonical_label("other") == "Other"
    assert canonical_label("NDA Request") is None


def test_a_purchase_without_vendor_value_or_date_is_refused():
    msg = refusal(**{**FULL, "counterparty": None, "estimated_value": " ", "needed_by": None})
    assert "vendor" in msg and "estimated value" in msg and "needed by" in msg


def test_department_and_a_real_description_are_always_required():
    msg = refusal(**{**FULL, "department": None, "description": "buy stuff"})
    assert "department" in msg and "what exactly is needed" in msg


def test_a_legal_question_needs_no_deal_facts():
    q = {**FULL, "type_label": "Legal Question — General", "counterparty": None,
         "estimated_value": None, "needed_by": None}
    assert refusal(**q) is None


def test_a_complete_request_may_be_filed_and_its_facts_reach_the_description():
    assert refusal(**FULL) is None
    block = details_block(counterparty="Boat", estimated_value="INR 3L", needed_by=None)
    assert "Counterparty: Boat" in block and "Estimated value: INR 3L" in block and "Needed by" not in block


def test_a_vendor_purchase_has_its_own_label_not_contract_review():
    """The chat filed purchases as 'Contract Review'; the wizard's labels are now the allowed ones."""
    assert canonical_label("new agreement · buying from a vendor") == "New agreement · Buying from a vendor"
    assert refusal(**{**FULL, "type_label": "New agreement · Buying from a vendor"}) is None
    assert canonical_label("Statement of Work Request") == "Statement of Work Request"
    assert canonical_label("Contract Review") is None


def test_free_ai_drafting_is_refused_until_the_user_chose_it():
    from app.ai.tool_registry import GenerateContractInput
    from app.intake.chat_filing import DRAFT_CHOICE_REFUSAL

    assert GenerateContractInput(title="t", instructions="i").user_chose_free_drafting is False
    assert "template" in DRAFT_CHOICE_REFUSAL and "user_chose_free_drafting=true" in DRAFT_CHOICE_REFUSAL


def test_template_draft_input_only_accepts_templates_we_have():
    """draft_from_template must not be asked for a lease: there is no template for one."""
    import pytest
    from pydantic import ValidationError

    from app.ai.tool_registry import DraftFromTemplateInput

    assert DraftFromTemplateInput(doc_type="nda", counterparty="Acme").doc_type == "nda"
    with pytest.raises(ValidationError):
        DraftFromTemplateInput(doc_type="lease", counterparty="Acme")


def test_a_needed_by_date_in_the_past_is_refused():
    """The model guessed 2024 for '16th November'; a past deadline must go back to it."""
    assert "in the past" in refusal(**{**FULL, "needed_by": "2024-11-16"})
    assert refusal(**{**FULL, "needed_by": "2999-11-16"}) is None


def test_counterparty_and_a_numeric_value_reach_the_fields_the_page_reads():
    """The request page showed 'Counterparty —' and 'Deal value Not stated' because chat put both only in the text."""
    from app.intake.chat_filing import known_fields

    assert known_fields(counterparty="Logitech", estimated_value="INR 300,000") == {
        "counterparty": "Logitech", "value": "300000", "currency": "INR"}
    assert known_fields(counterparty="not known", estimated_value="3 lakh") == {}
    assert "value" not in known_fields(counterparty=None, estimated_value="about three lakh")
