from dataclasses import dataclass
from typing import Type

from pydantic import BaseModel


@dataclass(frozen=True)
class SkillSpec:
    name: str
    version: str
    description: str
    execution_mode: str
    prompt_key: str
    prompt_version: str
    input_model: Type[BaseModel] | None
    output_model: Type[BaseModel]
    required_permission: str | None
    resource_type: str | None
    requires_citations: bool
    allows_mutation: bool
    feature_flag: str | None
    enabled_by_default: bool
    max_tokens: int = 2048
    temperature: float = 0.0
    timeout_seconds: int = 120

    @property
    def return_tool_name(self) -> str:
        return f"return_{self.name}"

    @property
    def output_schema_name(self) -> str:
        return self.output_model.__name__
