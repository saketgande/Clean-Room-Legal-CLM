import json
import logging
from dataclasses import dataclass
from typing import Any

from app.ai.context import ContractAIContext
from app.ai.prompt_versions import PromptBundle
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
            # The model sees the input as given; redaction happens only where it is
            # stored (controller._redacted_input), never in the prompt itself.
            json.dumps(input_payload, indent=2, sort_keys=True, default=str),
        ]
        logging.debug("Building prompt for skill %s v%s with input %s", spec.name, spec.version, input_payload)
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


prompt_builder = PromptBuilder()
