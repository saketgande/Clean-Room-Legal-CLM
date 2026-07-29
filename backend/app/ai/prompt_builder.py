import json
from dataclasses import dataclass
from typing import Any

from app.ai.context import ContractAIContext
from app.ai.prompt_versions import PromptBundle
from app.ai.redaction import SENSITIVE_KEYS
from app.ai.skill import SkillSpec


@dataclass(frozen=True)
class BuiltPrompt:
    system_prompt: str
    user_prompt: str
    context_manifest: dict[str, Any]


class PromptBuilder:
    def build_structured_skill_prompt(
        self,
        *,
        spec: SkillSpec,
        prompt_bundle: PromptBundle,
        input_payload: dict[str, Any],
        contract_context: ContractAIContext | None = None,
    ) -> BuiltPrompt:
        sections = [
            f"Skill: {spec.name} v{spec.version}",
            prompt_bundle.skill_prompt,
            "Input payload:",
            json.dumps(_redacted_payload(input_payload), indent=2, sort_keys=True, default=str),
        ]
        manifest: dict[str, Any] = {"skill_name": spec.name}
        if contract_context is not None:
            manifest.update(contract_context.manifest)
            sections.extend(
                [
                    "Contract context metadata:",
                    json.dumps(contract_context.manifest, indent=2, sort_keys=True),
                    "Untrusted contract text:",
                    contract_context.text,
                ]
            )
        sections.append(
            "Return data that matches the supplied output schema. Use null or empty lists when the answer is not found."
        )
        return BuiltPrompt(
            system_prompt=prompt_bundle.shared_system_prompt,
            user_prompt="\n\n".join(sections),
            context_manifest=manifest,
        )


def _redacted_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Placeholder-out raw document bodies the model doesn't need duplicated
    (already covered by `contract_context.text` when present) — NOT a filter
    on what the model receives generally. Must match `SENSITIVE_KEYS` exactly:
    a substring check like `"text" in key.lower()` also matches keys such as
    `retrieved_context` or `table_context` — a skill's actual retrieved
    content — and silently replaces it with a length-only placeholder in the
    real prompt, leaving the model to answer with no context at all.
    """
    redacted = dict(payload)
    for key in list(redacted.keys()):
        if key.lower() in SENSITIVE_KEYS and isinstance(redacted[key], str) and len(redacted[key]) > 500:
            redacted[key] = f"<redacted text length={len(redacted[key])}>"
    return redacted


prompt_builder = PromptBuilder()
