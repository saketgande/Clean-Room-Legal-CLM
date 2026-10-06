"""The AI feature registry: one entry for every place the app calls Claude.

A *feature* is one AI job the product does ("intake triage", "clause
extraction"). Each entry says, in one place, everything the gateway needs to
run that job the same way every time:

* which prompt to load (``prompt_key``; the text itself lives in the prompt
  table / ``prompt_versions.DEFAULT_SKILL_PROMPTS``, not here),
* how the system prompt is laid out (``layout``, see below),
* whether the anti-injection guard is appended (``guard``),
* the reply budget (``max_tokens``, ``temperature``),
* the structured-output contract (``tool_name`` + ``output_model``), and
* the label and category the AI Usage & Cost page shows.

The registry is *built* from the two lists that already exist, so it can't
drift from them:

* ``app.ai.registry.skill_registry`` — the AIController skills, and
* ``app.ai.agent_catalog.STANDALONE_AGENTS`` — the standalone agents.

Phase 1 of the gateway rollout (see the "AI gateway" section of
backend/ARCHITECTURE.md): this module exists beside today's call paths and
Phase 2 added the three "task" features that used to call the client
directly with hardcoded prompts (renewal recommendation, playbook expansion,
trademark journal reading); their prompts now live in the prompt table.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel


class PromptLayout(StrEnum):
    # Standalone agents: system prompt = the feature's own prompt (+ guard).
    FEATURE_SYSTEM = "feature_system"
    # AIController skills: system prompt = SHARED_LEGAL_SYSTEM_PROMPT (which
    # already carries the untrusted-content rules); the feature's prompt goes
    # into the user turn, built by app.ai.prompt_builder.
    SHARED_SYSTEM = "shared_system"


class FeatureKind(StrEnum):
    SKILL = "skill"
    AGENT = "agent"
    # A job that is neither an AIController skill nor a catalogued agent; its
    # call shape is declared once here (see _TASKS).
    TASK = "task"


@dataclass(frozen=True)
class AIFeature:
    key: str
    kind: FeatureKind
    label: str
    category: str
    prompt_key: str
    default_prompt_version: str
    layout: PromptLayout
    guard: bool
    max_tokens: int
    temperature: float
    # Structured output. tool_name is the forced tool Claude must "call";
    # output_model (pydantic) validates what comes back. A feature with a
    # tool_name but no output_model gets a shape check only (a dict came back).
    tool_name: str | None = None
    output_model: type[BaseModel] | None = None
    # None = use the prompt bundle's model (an admin's prompt-table override,
    # else settings.claude_model).
    model: str | None = None


# Cost-page wording, keyed by feature key: (label, category). The first block
# mirrors app.analytics.routes._LABELS exactly (a test keeps them in step until
# the analytics page reads from here); the rest are features that page shows
# today only as a title-cased prompt key under "Other".
_LABELS: dict[str, tuple[str, str]] = {
    "intake_triage": ("Intake triage", "Raise a request"),
    "contract_metadata_extraction": ("Metadata extraction", "Raise a request"),
    "contract_docx_generation": ("Auto-draft generation", "Raise a request"),
    "contract_edit_suggestions": ("Redline suggestions", "Redlining"),
    "playbook_review": ("Playbook deviation review", "Redlining"),
    "playbook_generation": ("Playbook generation", "Redlining"),
    "assistant_streaming": ("Ask Aegis (chat turn)", "Ask Aegis / Chat"),
    "contract_brain_answer": ("Contract Brain answer", "Ask Aegis / Chat"),
    "tabular_review_chat": ("Tabular review chat", "Ask Aegis / Chat"),
    "flow_router": ("Workflow router", "Ask Aegis / Chat"),
    "clause_extraction": ("Clause extraction", "Analysis & extraction"),
    "clause_labeling": ("Clause labelling", "Analysis & extraction"),
    "contract_risk_assessment": ("Risk assessment", "Analysis & extraction"),
    "tabular_cell_extraction": ("Tabular cell extraction", "Analysis & extraction"),
    "tabular_row_extraction": ("Tabular row extraction", "Analysis & extraction"),
    "obligation_extraction": ("Obligation extraction", "Analysis & extraction"),
    "renewal_extraction": ("Renewal extraction", "Analysis & extraction"),
    "plain_language_summary": ("Plain-language summary", "Analysis & extraction"),
    "notice_response_agent": ("Notice response", "Analysis & extraction"),
    # Not on the cost page's list today.
    "playbook_chat_build": ("Playbook builder chat", "Redlining"),
    "playbook_recommendations": ("Playbook recommendations", "Redlining"),
    "privacy_incident_assessment": ("Privacy incident assessment", "Analysis & extraction"),
    "intake_form_read": ("Intake form read", "Raise a request"),
    "litigation_intake_agent": ("Litigation assessment", "Raise a request"),
    "email_triage_agent": ("Email triage", "Raise a request"),
    "notice_extraction_agent": ("Notice extraction", "Analysis & extraction"),
    # Task features (Phase 2).
    "renewal_recommendation": ("Renewal recommendation", "Analysis & extraction"),
    "playbook_expand": ("Playbook expansion", "Redlining"),
    "trademark_journal_vision": ("Trademark journal reading", "Analysis & extraction"),
    "clause_hierarchy": ("Clause hierarchy", "Analysis & extraction"),
}

# Each standalone agent's call shape (forced tool name, reply budget). These
# are the values the agent modules sent before Phase 3 moved them onto the
# gateway; this table is now their only home. None = a plain-text agent.
_AGENT_CALL_SHAPE: dict[str, tuple[str | None, int]] = {
    "flow_router": ("suggest_flow", 600),
    "intake_triage": ("triage_request", 900),
    "intake_form_read": ("read_request", 700),
    "litigation_intake_agent": ("assess_litigation", 900),
    "email_triage_agent": ("classify_email", 200),
    "plain_language_summary": (None, 500),
    "notice_extraction_agent": ("extract_notice_fields", 700),
    "notice_response_agent": (None, 1200),
}


# Task features: (key, tool_name, max_tokens, temperature). Values are what the
# call sites sent before they moved onto the gateway.
_TASKS: tuple[tuple[str, str, int, float], ...] = (
    ("renewal_recommendation", "recommend_renewal", 400, 0.2),
    ("playbook_expand", "draft_playbook_rules", 2500, 0.3),
    ("trademark_journal_vision", "extract_journal_entries", 4000, 0.0),
    # One short row per undecided clause: 4000 covers ~150 questions. The old
    # call sized it as 512 + 24 per question; a cut-off answer now fails the
    # call (rules only) instead of leaving the unanswered rows undecided.
    ("clause_hierarchy", "clause_parents", 4000, 0.0),
)


class UnknownAIFeature(KeyError):
    pass


class FeatureRegistry:
    def __init__(self, features: list[AIFeature]) -> None:
        self._by_key: dict[str, AIFeature] = {}
        for feature in features:
            if feature.key in self._by_key:
                raise ValueError(f"Duplicate AI feature key: {feature.key}")
            self._by_key[feature.key] = feature

    def get(self, key: str) -> AIFeature:
        try:
            return self._by_key[key]
        except KeyError:
            raise UnknownAIFeature(f"No AI feature registered as {key!r}") from None

    def all(self) -> list[AIFeature]:
        return list(self._by_key.values())

    def labels(self) -> dict[str, tuple[str, str]]:
        """prompt_key -> (label, category), the shape the cost page uses."""
        return {f.prompt_key: (f.label, f.category) for f in self._by_key.values()}


def _label(key: str) -> tuple[str, str]:
    return _LABELS.get(key, (key.replace("_", " ").title(), "Other"))


def build_feature_registry() -> FeatureRegistry:
    from app.ai.agent_catalog import STANDALONE_AGENTS
    from app.ai.registry import skill_registry

    features: list[AIFeature] = []
    for spec in skill_registry.all():
        label, category = _label(spec.name)
        features.append(
            AIFeature(
                key=spec.name,
                kind=FeatureKind.SKILL,
                label=label,
                category=category,
                prompt_key=spec.prompt_key,
                default_prompt_version=spec.prompt_version,
                layout=PromptLayout.SHARED_SYSTEM,
                # The shared legal system prompt already states the
                # untrusted-content rule; skills never had the agent guard.
                guard=False,
                max_tokens=spec.max_tokens,
                temperature=spec.temperature,
                tool_name=spec.return_tool_name,
                output_model=spec.output_model,
            )
        )
    for agent in STANDALONE_AGENTS:
        label, category = _label(agent.id)
        tool_name, max_tokens = _AGENT_CALL_SHAPE[agent.id]
        features.append(
            AIFeature(
                key=agent.id,
                kind=FeatureKind.AGENT,
                label=label,
                category=category,
                prompt_key=agent.prompt_key,
                # get_agent_prompt's default_version today.
                default_prompt_version="1.0.0",
                layout=PromptLayout.FEATURE_SYSTEM,
                guard=True,
                max_tokens=max_tokens,
                temperature=agent.temperature,
                tool_name=tool_name,
            )
        )
    for key, tool_name, max_tokens, temperature in _TASKS:
        label, category = _label(key)
        features.append(
            AIFeature(
                key=key,
                kind=FeatureKind.TASK,
                label=label,
                category=category,
                prompt_key=key,
                default_prompt_version="1.0.0",
                layout=PromptLayout.FEATURE_SYSTEM,
                guard=True,
                max_tokens=max_tokens,
                temperature=temperature,
                tool_name=tool_name,
            )
        )
    return FeatureRegistry(features)


feature_registry = build_feature_registry()
