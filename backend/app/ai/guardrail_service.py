"""NeMo Guardrails for the legal assistant.

Every check here is a built-in NeMo Guardrails rail configured in
``app/ai/guardrails/config.yml``:

* input rails    : context bloat detection, detect sensitive
                   data on input, toxicity (hf classifier), content safety, self check
                   input, topic safety
* retrieval rails: context bloat detection on retrieval (contract text sent to the model)
* output rails   : mask sensitive data on output, toxicity, content safety, injection
                   detection, self check output, self check facts

Nothing in this module judges content itself; it only runs the rails and reports the
verdict. ``self check facts`` needs evidence; callers pass it to
``apply_output_guardrails`` and NeMo skips that rail when it is absent. Callers own
audit/persistence. Under MOCK_GUARDRAILS (CI / dev without a key) the rails are skipped.
"""

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.audit import write_audit_log
from app.core.config import settings

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "guardrails"

# What a block means to the user, keyed by the rail that fired (NeMo reports the flow
# name, e.g. "topic safety check input $model=topic_control").
_REASONS = (
    ("context bloat", "it was oversized or padded with repetitive content"),
    ("detect sensitive data", "it contains sensitive personal or financial data"),
    ("topic safety", "it is outside the topics this assistant supports (contracts and legal operations)"),
    ("content safety", "it contains unsafe content"),
    ("injection detection", "it contained code, template or script injection patterns"),
    ("self check facts", "its claims are not supported by the contract text it was based on"),
    ("hf classifier", "it was classified as toxic or abusive language"),
    (
        "self check",
        (
            "it appears to violate Clean-Room compliance policy (restricted contact details, "
            "confidential pricing or compensation, cross-tenant / ethical-wall data, or an attempt "
            "to override system rules)"
        ),
    ),
)


def _rail_label(rail: str) -> str:
    """Strip NeMo flow parameters: 'topic safety check input $model=x' -> 'topic safety check input'."""
    return rail.split(" $")[0].strip()


def _reason_for(rail: str) -> str:
    lowered = rail.lower()
    for prefix, reason in _REASONS:
        if lowered.startswith(prefix):
            return reason
    return "it violates the assistant's guardrail policy"


def _input_block_message(rail: str) -> str:
    return f"Query blocked by NeMo Guardrails ({_rail_label(rail)}): {_reason_for(rail)}."


def _output_block_message(rail: str) -> str:
    return f"Response withheld by NeMo Guardrails ({_rail_label(rail)}): {_reason_for(rail)}."


UNAVAILABLE_MESSAGE = (
    "Query blocked by NeMo Guardrails: the compliance check could not be completed, "
    "so the request was not processed. Please try again shortly."
)


@dataclass(frozen=True)
class GuardrailResult:
    allowed: bool
    # Allowed: the text to use (NeMo may rewrite it). Blocked: the message to show the user.
    message: str = ""
    # "nemo_input" | "nemo_output" | "unavailable" - for audit; never contains user text.
    reason: str | None = None
    rail: str | None = None


def _strip_code_fence(text: str) -> str:
    """Claude sometimes wraps a JSON verdict in a ```json fence; NeMo's parsers want bare JSON."""
    match = re.match(r"^\s*```[a-zA-Z]*\s*(.*?)\s*```\s*$", text, re.DOTALL)
    return match.group(1) if match else text


def _register_fence_tolerant_parsers(rails) -> None:
    """Plumbing only: unwrap the fence, then let NeMo's own content-safety parsers decide."""
    from nemoguardrails.llm.output_parsers import (
        nemoguard_parse_prompt_safety,
        nemoguard_parse_response_safety,
    )

    rails.register_output_parser(
        lambda text: nemoguard_parse_prompt_safety(_strip_code_fence(text)),
        "nemoguard_parse_prompt_safety_unfenced",
    )
    rails.register_output_parser(
        lambda text: nemoguard_parse_response_safety(_strip_code_fence(text)),
        "nemoguard_parse_response_safety_unfenced",
    )


def _register_remote_sensitive_data_actions(rails) -> None:
    """Run NeMo's sensitive-data rail on the host's guardrail-models server instead of in this
    process, so the spaCy/Presidio model is not loaded here. Same detect/mask semantics as NeMo's
    own actions (detect: the rail's score threshold; mask: NeMo's 0.4 default)."""
    import httpx
    from nemoguardrails.actions import action
    from nemoguardrails.library.sensitive_data_detection.actions import (
        _mask_sensitive_data_outcome,
        _sensitive_data_detection_outcome,
    )

    base_url = settings.guardrail_models_url.rstrip("/")

    async def _post(path: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{base_url}{path}", json=payload)
            response.raise_for_status()
            return response.json()

    @action(is_system_action=True)
    async def detect_sensitive_data(source: str, text: str, config, **kwargs):
        options = getattr(config.rails.config.sensitive_data_detection, source)
        found = await _post(
            "/pii/detect",
            {"text": text, "entities": options.entities, "score_threshold": options.score_threshold},
        )
        return _sensitive_data_detection_outcome(found["has_sensitive_data"])

    @action(is_system_action=True)
    async def mask_sensitive_data(source: str, text: str, config, **kwargs):
        options = getattr(config.rails.config.sensitive_data_detection, source)
        masked = await _post("/pii/mask", {"text": text, "entities": options.entities})
        return _mask_sensitive_data_outcome(source, text, masked["text"])

    rails.register_action(detect_sensitive_data, "detect_sensitive_data")
    rails.register_action(mask_sensitive_data, "mask_sensitive_data")


@lru_cache(maxsize=1)
def get_rails_instance():
    # Imported lazily: nemoguardrails is heavy and not needed under MOCK_GUARDRAILS.
    os.environ.setdefault("NEMOGUARDRAILS_LLM_FRAMEWORK", "langchain")
    if settings.claude_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.claude_api_key)
    from nemoguardrails import LLMRails, RailsConfig

    # NeMo logs every prompt and completion (user text, privileged content) at INFO.
    logging.getLogger("nemoguardrails").setLevel(logging.WARNING)

    config = RailsConfig.from_path(str(CONFIG_PATH))
    for model in config.models:
        model.model = settings.guardrails_model
    config.rails.config.hf_classifier["toxicity"].base_url = settings.guardrail_models_url.rstrip("/")
    rails = LLMRails(config)
    _register_fence_tolerant_parsers(rails)
    _register_remote_sensitive_data_actions(rails)
    return rails


async def _check(messages: list[dict], rail_type_name: str):
    from nemoguardrails.rails.llm.options import RailType

    rails = get_rails_instance()
    return await rails.check_async(messages=messages, rail_types=[RailType(rail_type_name)])


def _failure(text: str) -> GuardrailResult:
    logger.exception("NeMo guardrail evaluation failed")
    if settings.guardrails_fail_closed:
        return GuardrailResult(False, UNAVAILABLE_MESSAGE, "unavailable")
    return GuardrailResult(True, text)


async def apply_input_guardrails(prompt_text: str) -> GuardrailResult:
    if not prompt_text or not prompt_text.strip() or settings.mock_guardrails:
        return GuardrailResult(True, prompt_text)
    try:
        result = await _check([{"role": "user", "content": prompt_text}], "input")
    except Exception:
        return _failure(prompt_text)
    if result.status.value == "blocked":
        rail = result.rail or "self check input"
        logger.warning("NeMo input rail %r blocked a message (chars=%d)", rail, len(prompt_text))
        return GuardrailResult(False, _input_block_message(rail), "nemo_input", rail)
    return GuardrailResult(True, prompt_text)


_MAX_EVIDENCE_CHARS = 60000


def _output_messages(user_text: str, answer: str, evidence: str | None) -> list[dict]:
    """Messages for the output rails. NeMo reads ``relevant_chunks`` from a leading context
    message and only runs ``self check facts`` when ``check_facts`` is set."""
    messages = [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": answer},
    ]
    context: dict = {}
    if evidence and evidence.strip():
        # NeMo keeps relevant_chunks as one string (its own retrieval step appends to it).
        context["relevant_chunks"] = evidence[:_MAX_EVIDENCE_CHARS]
        context["check_facts"] = True
    if context:
        messages.insert(0, {"role": "context", "content": context})
    return messages


async def apply_output_guardrails(
    user_text: str,
    answer: str,
    *,
    evidence: str | None = None,
) -> GuardrailResult:
    if not answer or not answer.strip() or settings.mock_guardrails:
        return GuardrailResult(True, answer)
    try:
        result = await _check(_output_messages(user_text, answer, evidence), "output")
    except Exception:
        return _failure(answer)
    if result.status.value == "blocked":
        rail = result.rail or "self check output"
        logger.warning("NeMo output rail %r withheld an answer (chars=%d)", rail, len(answer))
        return GuardrailResult(False, _output_block_message(rail), "nemo_output", rail)
    return GuardrailResult(True, result.content or answer)


async def apply_retrieval_guardrails(question: str, retrieved_text: str) -> GuardrailResult:
    """Run NeMo's context bloat detection over the contract text about to be sent to the
    model. NeMo only runs its retrieval *flows* inside its own answer-generation pipeline,
    which this app does not use, so this calls the rail's detection action directly."""
    if not retrieved_text or not retrieved_text.strip() or settings.mock_guardrails:
        return GuardrailResult(True, retrieved_text)
    rail = "context bloat detection on retrieval"
    try:
        from nemoguardrails.library.context_bloat_detection.actions import context_bloat_detection

        outcome = await context_bloat_detection(
            source="retrieval", text=retrieved_text, config=get_rails_instance().config
        )
    except Exception:
        return _failure(retrieved_text)
    if outcome.is_blocked:
        logger.warning(
            "NeMo retrieval rail %r blocked retrieved text (chars=%d, reason=%s, metrics=%s)",
            rail,
            len(retrieved_text),
            getattr(outcome, "reason", None),
            (getattr(outcome, "metadata", None) or {}).get("metrics"),
        )
        return GuardrailResult(
            False,
            f"Query blocked by NeMo Guardrails ({rail}): the retrieved contract text "
            "was oversized or padded, so it was not sent to the model.",
            "nemo_retrieval",
            rail,
        )
    return GuardrailResult(True, retrieved_text)


def audit_guardrail_block(
    db: Session,
    *,
    result: GuardrailResult,
    text_chars: int,
    org_id: str,
    actor_user_id: str,
    request_id: str | None,
    resource_type: str,
    resource_id: str | None = None,
) -> None:
    """Text-free audit row for a block. Caller commits."""
    write_audit_log(
        db,
        action="assistant.guardrail_blocked",
        resource_type=resource_type,
        resource_id=resource_id,
        org_id=org_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        metadata={"reason": result.reason, "rail": result.rail, "message_chars": text_chars},
    )


async def enforce_input_guardrails(
    db: Session,
    *,
    text: str,
    org_id: str,
    actor_user_id: str,
    request_id: str | None,
    resource_type: str,
    resource_id: str | None = None,
) -> None:
    """Gate for non-streaming endpoints taking free text for the model.

    On a block: audits, commits, and raises a 422 naming the rail. Nothing the user
    typed is persisted.
    """
    result = await apply_input_guardrails(text)
    if result.allowed:
        return
    audit_guardrail_block(
        db,
        result=result,
        text_chars=len(text),
        org_id=org_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    db.commit()
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.message)


async def enforce_output_guardrails(
    db: Session,
    *,
    user_text: str,
    answer: str,
    org_id: str,
    actor_user_id: str,
    request_id: str | None,
    resource_type: str,
    resource_id: str | None = None,
    evidence: str | None = None,
) -> None:
    """Output-rail counterpart of ``enforce_input_guardrails`` for non-streaming answers."""
    result = await apply_output_guardrails(user_text, answer, evidence=evidence)
    if result.allowed:
        return
    audit_guardrail_block(
        db,
        result=result,
        text_chars=len(answer),
        org_id=org_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    db.commit()
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.message)


async def enforce_retrieval_guardrails(
    db: Session,
    *,
    question: str,
    retrieved_text: str,
    org_id: str,
    actor_user_id: str,
    request_id: str | None,
    resource_type: str,
    resource_id: str | None = None,
) -> None:
    """Gate for retrieved contract text before it is sent to the model."""
    result = await apply_retrieval_guardrails(question, retrieved_text)
    if result.allowed:
        return
    audit_guardrail_block(
        db,
        result=result,
        text_chars=len(retrieved_text),
        org_id=org_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    db.commit()
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.message)
