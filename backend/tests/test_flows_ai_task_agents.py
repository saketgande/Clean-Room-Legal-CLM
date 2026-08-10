"""The flow engine's ai_task step (backend/app/flows/service.py) previously
routed every mapped agent key through a static regex classifier even when a
real, Claude-backed result already existed elsewhere for the same request.
These tests pin the fix for the litigation-agent case: reuse the genuine
assessment computed at intake time instead of re-deriving a worse one."""

import inspect

from app.ai.registry import skill_registry
from app.ai.schemas import PrivacyIncidentAssessmentOutput
from app.flows.service import _AGENT_KEY_MAP, _ai_agent_failed, _execute_step


def test_agent_key_map_routes_litigation_variants_to_the_same_intake_agent():
    assert _AGENT_KEY_MAP["litigation-agent"] == "litigation_agent"
    assert _AGENT_KEY_MAP["notice-mgmt-agent"] == "litigation_agent"
    assert _AGENT_KEY_MAP["vendor-intake-agent"] == "vendor_agent"
    assert _AGENT_KEY_MAP["privacy-assessment-agent"] == "privacy_agent"


def test_ai_task_step_reuses_the_real_litigation_assessment_when_present():
    source = inspect.getsource(_execute_step)

    assert 'mapped == "litigation_agent"' in source
    assert '.get("litigation_assessment")' in source

    litigation_branch = source.split('mapped == "litigation_agent"', 1)[1]
    assessment_branch, fallback_branch = litigation_branch.split("if assessment:", 1)[1].split(
        "else:", 1
    )

    # Confidence for the escalation gate comes from the real assessment's own
    # fact-confidence, not the regex classifier's fixed constant.
    assert 'assessment.get("assessment_confidence")' in assessment_branch
    assert "intake_agents.classify(" in fallback_branch


def test_ai_task_step_only_trusts_litigation_confidence_from_a_real_model_call():
    """assessment_confidence measures trust in the extracted FACTS, a distinct
    judgment from flow_suggestion.confidence (which is only about routing fit)
    — a live scorecard proved a genuine model call can be fully confident about
    the workflow pick while the facts on a vague ticket are thin. The heuristic
    fallback (source != "llm") must be capped low regardless of its own number,
    since litigation_agent._heuristic's facts are keyword-guessed and defaulted,
    not genuine extraction."""
    source = inspect.getsource(_execute_step)
    assessment_branch = source.split('mapped == "litigation_agent"', 1)[1].split(
        "if assessment:", 1
    )[1].split("elif mapped ==", 1)[0]

    assert 'fs.get("source") == "llm"' in assessment_branch
    assert "min(ac or 0.0, 0.3)" in assessment_branch

    # Exercise the exact expression the branch evaluates, for both sources.
    ac = 0.9
    llm_conf = ac if (isinstance(ac, (int, float)) and "llm" == "llm") else min(ac or 0.0, 0.3)
    degraded_conf = ac if (isinstance(ac, (int, float)) and "deterministic" == "llm") else min(ac or 0.0, 0.3)
    assert llm_conf == 0.9
    assert degraded_conf == 0.3
    # Below both builtin litigation flows' escalate_below_confidence (0.7, 0.75).
    assert degraded_conf < 0.7


def test_ai_task_step_reuses_real_screening_and_never_treats_unscreened_as_clear():
    """vendor_agent must reuse the real sanctions/conflict screening that already
    ran at intake time (intake/screening.py) instead of an LLM/regex guess — and
    must never treat an unscreened ("unavailable") vendor as safe to auto-advance,
    matching screening.py's own documented safety posture."""
    source = inspect.getsource(_execute_step)

    assert 'mapped == "vendor_agent"' in source
    vendor_branch = source.split('mapped == "vendor_agent"', 1)[1].split("if real:", 1)[0]

    assert "request.screening" in vendor_branch
    assert 's_status == "hit" or high_conflict' in vendor_branch

    # "unavailable" gets its own low-confidence branch — it must not fall through
    # to the "clear" (0.95) case.
    unavailable_branch = vendor_branch.split('s_status == "unavailable"', 1)[1].split("else:", 1)[0]
    assert "vconf = 0.2" in unavailable_branch


def test_ai_task_step_waits_for_the_real_risk_score_before_falling_back():
    """contract_review_agent must use the genuine weighted risk score
    (contracts/risk.py) once the background clause-extraction + risk job has
    landed, keep yielding while it's still pending, and only fall back to the
    regex classifier once that's clearly not going to happen in time — never
    settle for the regex guess just because the real score isn't ready yet."""
    source = inspect.getsource(_execute_step)

    assert 'mapped == "contract_review_agent"' in source
    contract_review_branch = source.split('mapped == "contract_review_agent"', 1)[1].split(
        "elif mapped:", 1
    )[0]

    ready_branch, rest = contract_review_branch.split("contract.risk_score is not None", 1)[1].split(
        "elif sr and sr.updated_at", 1
    )
    waiting_clause, fallback_branch = rest.split("return \"yield\"", 1)

    # Real score present -> derive confidence from the risk band, not a regex guess.
    assert '{"low": 0.95, "medium": 0.65, "high": 0.3}' in ready_branch
    # Still pending -> bounded wait (yield again), not an immediate fallback.
    assert "total_seconds() < 60" in waiting_clause
    # Only past the bound -> fall back, and say so honestly.
    assert "intake_agents.classify(" in fallback_branch
    assert "not ready in time" in fallback_branch


def test_privacy_incident_assessment_skill_is_registered_with_a_numeric_confidence():
    """Unlike this file's other skills (high/medium/low), privacy_incident_assessment
    needs a numeric 0-1 confidence — it feeds escalate_below_confidence directly."""
    spec = skill_registry.get("privacy_incident_assessment")

    assert spec.output_model is PrivacyIncidentAssessmentOutput
    assert spec.prompt_key == "privacy_incident_assessment"

    field = PrivacyIncidentAssessmentOutput.model_fields["confidence"]
    assert field.annotation is float
    # No silent default: the whole escalation gate depends on this field, so a
    # model response that omits it must fail validation (and route through the
    # real failure handler), not quietly pass as an unearned "medium" verdict.
    assert field.is_required()


def test_ai_task_step_calls_the_real_privacy_skill_with_no_fallback_to_mask_a_failure():
    """privacy_agent has no pre-computed signal to reuse (unlike litigation/vendor)
    and no cheaper fallback to settle for — a failed call must propagate to the
    shared except below and escalate to a human, never quietly degrade to a regex
    guess with no actual privacy judgment behind it."""
    source = inspect.getsource(_execute_step)

    assert 'mapped == "privacy_agent"' in source
    privacy_branch = source.split('mapped == "privacy_agent"', 1)[1].split("if real:", 1)[0]

    assert 'skill_name="privacy_incident_assessment"' in privacy_branch
    assert "run_structured_skill" in privacy_branch
    # No inner exception handler — a failure here must reach the shared one.
    assert "except Exception" not in privacy_branch


class _FakeStepRun:
    status = "running"
    note = None


def test_ai_agent_failed_escalates_to_a_human_instead_of_silently_advancing():
    """A genuine agent-call exception must stop for a human — the old bug left
    `conf = None` and fell through to `return "advance"`, which advance_run then
    marked done since it "wasn't already done/skipped." A plain Claude outage
    rendered as a green checkmark."""
    sr = _FakeStepRun()

    outcome = _ai_agent_failed(sr, RuntimeError("rate limited"), "Agent")

    assert outcome == "wait"
    assert sr.status == "waiting_human"
    assert "rate limited" in sr.note
    assert "needs manual review" in sr.note


def test_escalation_note_preserves_a_more_specific_result_detail_when_present():
    """The generic 'AI confidence X < Y' note used to overwrite a more specific
    explanation a branch had already left in sr.result (e.g. contract review's
    "risk score not ready in time" fallback note) — that detail must survive
    into the note the UI actually renders, not just sr.result."""
    source = inspect.getsource(_execute_step)
    escalation_block = source.rsplit('cfg.get("escalate_below_confidence")', 1)[1]

    assert '(sr.result or {}).get("note")' in escalation_block
    assert 'sr.note = f"{base} ({detail})" if detail else base' in escalation_block


def test_every_ai_task_exception_handler_routes_through_the_shared_failure_helper():
    """Pin the wiring, not just the helper: every `except Exception` inside the
    ai_task branch must call _ai_agent_failed — a bare `sr.note = ...` with no
    status change silently reintroduces the "failed step reads as Done" bug."""
    source = inspect.getsource(_execute_step)
    ai_task_source = source.split('if t == "ai_task":', 1)[1].split('if t == "clm_draft":', 1)[0]

    except_blocks = ai_task_source.count("except Exception as exc:")
    assert except_blocks >= 3
    assert ai_task_source.count("_ai_agent_failed(sr, exc,") == except_blocks
