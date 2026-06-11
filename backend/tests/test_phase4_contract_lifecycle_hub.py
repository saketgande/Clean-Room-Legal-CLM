import inspect
from datetime import date

from app.ai.controller import ai_controller
from app.contracts.lifecycle import allowed_transitions_for
from app.contracts.schemas import ContractUpdate
from app.contracts.service import contract_hub_summary
from app.core.enums import ContractLifecycleStage


def test_contract_update_accepts_metadata_fields_used_by_ai_extraction():
    payload = ContractUpdate(
        value_amount=12000.50,
        currency="USD",
        effective_date=date(2026, 1, 1),
        expiration_date=date(2026, 12, 31),
    )

    assert payload.value_amount == 12000.50
    assert payload.currency == "USD"
    assert payload.effective_date == date(2026, 1, 1)
    assert payload.expiration_date == date(2026, 12, 31)


def test_lifecycle_state_machine_exposes_allowed_transitions():
    assert allowed_transitions_for(ContractLifecycleStage.INTAKE) == [
        ContractLifecycleStage.ACTIVE,
        ContractLifecycleStage.AI_REVIEW,
        ContractLifecycleStage.DRAFTING,
    ]
    assert ContractLifecycleStage.ARCHIVED not in allowed_transitions_for(
        ContractLifecycleStage.DRAFTING
    )


def test_contract_hub_summary_is_permission_aware_and_widget_backed():
    source = inspect.getsource(contract_hub_summary)

    assert "accessible_contract_filter(user)" in source
    assert "pending_approvals" in source
    assert "pending_signatures" in source
    assert "upcoming_renewals" in source
    assert "overdue_obligations" in source
    assert "recent_activity" in source


def test_assistant_prompt_includes_contract_status_context_without_ids():
    contract_id = "11111111-1111-1111-1111-111111111111"
    project_id = "22222222-2222-2222-2222-222222222222"

    prompt = ai_controller._assistant_user_prompt(
        message="What is the risk?",
        project_id=project_id,
        contract_id=contract_id,
        contract_ids=[contract_id],
        handles=[{"handle": "contract-0", "contract_id": contract_id}],
        contract_summaries=[
            {
                "handle": "contract-0",
                "title": "Vendor Agreement",
                "lifecycle_stage": "ai_review",
                "risk_level": "high",
                "counterparty_name": "Acme Inc.",
            }
        ],
    )

    assert "contract-0" in prompt
    assert "ai_review" in prompt
    assert "high" in prompt
    assert contract_id not in prompt
    assert project_id not in prompt
