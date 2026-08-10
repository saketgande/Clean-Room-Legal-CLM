"""A live LLM-judge scorecard found redlining, drafting, and playbook-generation
output read as generic textbook explanation rather than a lawyer's actual
opinion on the specific deal — and separately, playbook_generation fabricated
a whole clause type absent from its source document. Root cause: (1) the
contract context manifest only carried IDs/lengths, never the real deal facts
(counterparty, value, jurisdiction, risk) already sitting on the Contract row,
so a skill had nothing deal-specific to reason from; (2) the prompts never
asked for grounded, deal-specific reasoning or an explicit self-check against
inventing unsupported clauses. Pin both fixes."""

from app.ai.context import build_contract_context
from app.ai.prompt_versions import DEFAULT_SKILL_PROMPTS
from app.contracts.models import Contract


def test_contract_context_manifest_carries_the_real_deal_facts():
    contract = Contract(
        id="c1", org_id="org1", title="Mutual MSA", contract_type="msa",
        counterparty_name="Acme Corp", jurisdiction="Delaware",
        value_amount=250000.0, currency="USD", risk_band="medium",
        risk_summary={"summary": "Balanced mutual obligations, moderate liability exposure."},
        owner_user_id="u1",
    )

    class _FakeDB:
        def get(self, model, obj_id):
            return contract if model is Contract else None

    ctx = build_contract_context(_FakeDB(), org_id="org1", contract_id="c1")

    assert ctx.manifest["counterparty_name"] == "Acme Corp"
    assert ctx.manifest["jurisdiction"] == "Delaware"
    assert ctx.manifest["value_amount"] == 250000.0
    assert ctx.manifest["risk_band"] == "medium"
    assert ctx.manifest["risk_summary"] == "Balanced mutual obligations, moderate liability exposure."


def test_contract_edit_suggestions_demands_grounded_asymmetric_reasoning():
    prompt = DEFAULT_SKILL_PROMPTS["contract_edit_suggestions"]
    assert "contract context metadata" in prompt
    assert "asymmetric" in prompt.lower()
    assert len(prompt) > 400


def test_contract_docx_generation_demands_real_sections_not_a_skeleton():
    prompt = DEFAULT_SKILL_PROMPTS["contract_docx_generation"]
    assert "never a skeleton of headers" in prompt
    assert "never invent a specific one" in prompt
    assert len(prompt) > 400


def test_playbook_generation_requires_pointing_to_real_source_language():
    prompt = DEFAULT_SKILL_PROMPTS["playbook_generation"]
    assert "confirm you can point to the specific source language" in prompt
    assert "not a generic explanation" in prompt
