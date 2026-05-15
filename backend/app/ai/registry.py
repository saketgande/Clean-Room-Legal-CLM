from app.ai.schemas import (
    AssistantAnswerOutput,
    ClauseExtractionOutput,
    ContractDocxGenerationOutput,
    ContractEditSuggestionsOutput,
    ContractMetadataOutput,
    SkillInfo,
)
from app.ai.skill import SkillSpec


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillSpec] = {}

    def register(self, spec: SkillSpec) -> None:
        if spec.name in self._skills:
            raise RuntimeError(f"Duplicate AI skill registered: {spec.name}")
        if not spec.prompt_key or not spec.prompt_version:
            raise RuntimeError(f"AI skill {spec.name} is missing prompt metadata")
        self._skills[spec.name] = spec

    def get(self, name: str) -> SkillSpec:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"Unknown AI skill: {name}") from exc

    def all(self) -> list[SkillSpec]:
        return sorted(self._skills.values(), key=lambda spec: spec.name)

    def public_info(self) -> list[SkillInfo]:
        return [
            SkillInfo(
                name=spec.name,
                version=spec.version,
                execution_mode=spec.execution_mode,
                prompt_key=spec.prompt_key,
                prompt_version=spec.prompt_version,
                output_schema_name=spec.output_schema_name,
                required_permission=spec.required_permission,
                feature_flag=spec.feature_flag,
                enabled_by_default=spec.enabled_by_default,
                requires_citations=spec.requires_citations,
                allows_mutation=spec.allows_mutation,
            )
            for spec in self.all()
        ]


skill_registry = SkillRegistry()

for registered_spec in [
    SkillSpec(
        name="contract_metadata_extraction",
        version="1.0.0",
        description="Extract supported contract metadata with citations.",
        execution_mode="job",
        prompt_key="contract_metadata_extraction",
        prompt_version="1.0.0",
        input_model=None,
        output_model=ContractMetadataOutput,
        required_permission="contract:read",
        resource_type="contract",
        requires_citations=True,
        allows_mutation=True,
        feature_flag="feature.ai.metadata_extraction",
        enabled_by_default=True,
        max_tokens=2048,
    ),
    SkillSpec(
        name="clause_extraction",
        version="1.0.0",
        description="Extract key clauses for Contract Brain and review.",
        execution_mode="job",
        prompt_key="clause_extraction",
        prompt_version="1.0.0",
        input_model=None,
        output_model=ClauseExtractionOutput,
        required_permission="contract:read",
        resource_type="contract",
        requires_citations=True,
        allows_mutation=True,
        feature_flag="feature.ai.clause_extraction",
        enabled_by_default=True,
        max_tokens=4096,
    ),
    SkillSpec(
        name="assistant_streaming",
        version="1.0.0",
        description="Assistant chat with contract handles, citations, and tools.",
        execution_mode="assistant",
        prompt_key="assistant_streaming",
        prompt_version="1.0.0",
        input_model=None,
        output_model=AssistantAnswerOutput,
        required_permission="assistant:use",
        resource_type=None,
        requires_citations=False,
        allows_mutation=False,
        feature_flag="feature.ai.assistant_streaming",
        enabled_by_default=True,
        max_tokens=4096,
    ),
    SkillSpec(
        name="contract_docx_generation",
        version="1.0.0",
        description="Create backend-renderable DOCX generation plans.",
        execution_mode="assistant_tool",
        prompt_key="contract_docx_generation",
        prompt_version="1.0.0",
        input_model=None,
        output_model=ContractDocxGenerationOutput,
        required_permission="contract:create",
        resource_type="contract",
        requires_citations=False,
        allows_mutation=True,
        feature_flag="feature.ai.docx_generation",
        enabled_by_default=True,
        max_tokens=4096,
    ),
    SkillSpec(
        name="contract_edit_suggestions",
        version="1.0.0",
        description="Create tracked-change-ready contract edit suggestions.",
        execution_mode="assistant_tool",
        prompt_key="contract_edit_suggestions",
        prompt_version="1.0.0",
        input_model=None,
        output_model=ContractEditSuggestionsOutput,
        required_permission="contract:redline",
        resource_type="contract",
        requires_citations=True,
        allows_mutation=True,
        feature_flag="feature.ai.edit_suggestions",
        enabled_by_default=True,
        max_tokens=4096,
    ),
]:
    skill_registry.register(registered_spec)
