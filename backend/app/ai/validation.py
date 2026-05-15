from typing import Any, Type

from pydantic import BaseModel, ValidationError

from app.core.enums import AIValidationStatus


def validate_structured_output(output_model: Type[BaseModel], raw_output: dict[str, Any]) -> tuple[BaseModel | None, str, str | None]:
    try:
        return output_model.model_validate(raw_output), AIValidationStatus.VALID, None
    except ValidationError as exc:
        return None, AIValidationStatus.INVALID, str(exc)
