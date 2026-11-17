from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class SkillSpec:
    name: str
    version: str
    description: str
    execution_mode: str
    prompt_key: str
    prompt_version: str
    input_model: type[BaseModel] | None
    output_model: type[BaseModel]
    required_permission: str | None
    resource_type: str | None
    requires_citations: bool
    allows_mutation: bool
    feature_flag: str | None
    enabled_by_default: bool
    max_tokens: int = 2048
    temperature: float = 0.0
    timeout_seconds: int = 120
    # Append the contract's full text to the prompt. Off for a skill whose input
    # already carries exactly what it needs (clause_labeling sends segments).
    include_contract_text: bool = True
    # Guardrails at the LLM level: a skill that answers a typed question names the input_payload key
    # holding the typed text (checked before the model is called) and the output field holding its
    # prose reply (checked before it is returned). See AIController.run_structured_skill.
    user_text_key: str | None = None
    answer_key: str | None = None

    @property
    def return_tool_name(self) -> str:
        return f"return_{self.name}"

    @property
    def output_schema_name(self) -> str:
        return self.output_model.__name__
