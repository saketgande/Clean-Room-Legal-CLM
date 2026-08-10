import inspect
from io import BytesIO
from zipfile import ZipFile

from app.ai.registry import skill_registry
from app.ai.tool_registry import tool_registry
from app.ai.tool_runtime import tool_runtime
from app.playbooks.models import PlaybookRule
from app.playbooks.routes import (
    decide_deviation,
    get_playbook_run,
    list_playbook_runs,
    run_playbook,
)
from app.playbooks.service import (
    _build_playbook_redline_docx,
    evaluate_rules_against_text,
    execute_playbook_run,
    generated_default_rules,
)


def test_generated_playbook_rules_cover_core_clauses():
    rules = generated_default_rules(contract_type="msa", focus_areas=[])
    clause_types = {rule["clause_type"] for rule in rules}

    assert "confidentiality" in clause_types
    assert "limitation_of_liability" in clause_types
    assert "termination" in clause_types


def test_playbook_rule_engine_detects_required_and_prohibited_deviations():
    rules = [
        PlaybookRule(
            id="rule-required",
            clause_type="governing_law",
            rule_type="required_language",
            required_language="governing law",
            preferred_position="Add a governing law clause.",
            risk_level="medium",
            approval_required=False,
        ),
        PlaybookRule(
            id="rule-prohibited",
            clause_type="liability",
            rule_type="prohibited_language",
            prohibited_language="unlimited liability",
            preferred_position="Replace with a reasonable liability cap.",
            risk_level="high",
            approval_required=True,
        ),
    ]

    deviations = evaluate_rules_against_text(
        rules=rules,
        text="Supplier accepts unlimited liability for all claims.",
    )

    assert {deviation.rule.id for deviation in deviations} == {"rule-required", "rule-prohibited"}
    assert any(deviation.citation and deviation.citation["type"] == "text_match" for deviation in deviations)
    assert any(deviation.citation and deviation.citation["type"] == "playbook_rule" for deviation in deviations)


def test_playbook_redline_docx_contains_native_word_revision_markup():
    content = _build_playbook_redline_docx(
        contract_title="Vendor Agreement",
        base_version_number=1,
        source_text="Supplier accepts unlimited liability.",
        instructions="Replace unlimited liability with a reasonable cap.",
    )

    with ZipFile(BytesIO(content)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
        settings_xml = archive.read("word/settings.xml").decode("utf-8")

    assert "<w:trackRevisions" in settings_xml
    assert "<w:del " in document_xml
    assert "<w:delText" in document_xml
    assert "<w:ins " in document_xml
    assert "Replace unlimited liability with a reasonable cap." in document_xml


def test_playbook_review_ai_skill_is_registered():
    spec = skill_registry.get("playbook_review")

    assert spec.prompt_key == "playbook_review"
    assert spec.output_schema_name == "PlaybookReviewOutput"
    assert spec.required_permission == "playbook:run"
    assert spec.requires_citations is True


def test_phase5_assistant_tools_are_enabled_with_expected_policies():
    review = tool_registry.get("run_playbook_review")
    redline = tool_registry.get("redline_against_playbook")

    assert review.enabled_by_default is True
    assert review.required_permission == "playbook:run"
    assert review.requires_confirmation is False
    assert redline.enabled_by_default is True
    assert redline.required_permission == "contract:redline"
    assert redline.requires_confirmation is True


def test_phase5_rest_paths_enforce_contract_access_and_redline_permission():
    get_source = inspect.getsource(get_playbook_run)
    decide_source = inspect.getsource(decide_deviation)
    list_source = inspect.getsource(list_playbook_runs)
    run_source = inspect.getsource(run_playbook)

    assert "get_contract_for_user(db, contract_id=run.contract_id" in get_source
    assert "get_contract_for_user(db, contract_id=deviation.contract_id" in decide_source
    assert "accessible_contract_filter(current_user)" in list_source
    assert '_require_permission(current_user, "contract:read")' in run_source
    assert '_require_permission(current_user, "contract:redline")' in run_source


def test_execute_playbook_run_trusts_a_genuine_zero_deviation_ai_verdict():
    """A real Claude review that finds nothing wrong must not be silently
    replaced by the deterministic keyword engine — only an actual AI failure
    (ai_output is None) should trigger that fallback."""
    source = inspect.getsource(execute_playbook_run)

    assert "if ai_output is not None:" in source
    deviation_source_branch = source.split("if ai_output is not None:", 1)[1]
    assert 'deviation_source = "claude"' in deviation_source_branch.split("else:")[0]
    assert 'deviation_source = "deterministic"' in deviation_source_branch.split("else:")[1]


def test_phase5_assistant_tool_runtime_enforces_dual_permissions():
    source = inspect.getsource(tool_runtime._run_playbook_review)

    assert 'has_permission(user.permission_values, "contract:read")' in source
    assert 'has_permission(user.permission_values, "playbook:run")' in source
    assert 'has_permission(user.permission_values, "contract:redline")' in source


def test_assistant_playbook_review_tool_attempts_a_real_ai_review():
    """This tool previously called execute_playbook_run with no ai_output/
    ai_error at all, so asking the assistant to review or redline against a
    playbook always silently used the deterministic engine — never Claude,
    regardless of what the user asked for. Pin that it now attempts the real
    skill first, same as the /run route and auto_review_contract."""
    source = inspect.getsource(tool_runtime._run_playbook_review)

    assert 'skill_name="playbook_review"' in source
    assert "run_structured_skill" in source
    assert "ai_output=ai_output" in source
    assert "ai_error=ai_error" in source
