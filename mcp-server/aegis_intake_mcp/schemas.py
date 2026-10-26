"""Input models for the seven tools. Every model forbids unknown fields, so the generated JSON
schemas carry `"additionalProperties": false` and a misspelt argument is refused by name
instead of being silently dropped (FR-10, AC-30)."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import ToolError

# Request ids: the platform id, or a REQ-… reference (resolved by the tool, not here).
REQUEST_ID_PATTERN = r"^(REQ-\d{1,12}|[A-Za-z0-9-]{1,64})$"
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9._:-]{8,128}$"
FORM_KEY_PATTERN = r"^[a-z_]{1,60}$"

AnswerValue = str | int | float | bool | list[str] | None
RequestId = Annotated[str, Field(pattern=REQUEST_ID_PATTERN)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttachmentInput(_Strict):
    filename: str = Field(min_length=1, max_length=300)
    mime_type: str = Field(min_length=1, max_length=120)
    content_base64: str


class ListRequestFormsInput(_Strict):
    form_key: str | None = Field(default=None, pattern=FORM_KEY_PATTERN)
    counterparty_search: str | None = Field(default=None, max_length=100)


class CreateIntakeRequestInput(_Strict):
    form_key: str = Field(pattern=FORM_KEY_PATTERN)
    answers: dict[str, AnswerValue]
    subject: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    attachments: list[AttachmentInput] | None = None
    idempotency_key: str | None = Field(default=None, pattern=IDEMPOTENCY_KEY_PATTERN)


class GetIntakeRequestInput(_Strict):
    request_id: RequestId


class ListMyRequestsInput(_Strict):
    limit: int = Field(default=25, ge=1, le=100)


class GetRequestSlaInput(_Strict):
    request_id: RequestId


class GetRequestDocumentsInput(_Strict):
    request_id: RequestId
    include_text: bool = False


class AddDocumentInput(_Strict):
    request_id: RequestId
    filename: str = Field(min_length=1, max_length=300)
    mime_type: str = Field(min_length=1, max_length=120)
    content_base64: str


TOOL_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "list_request_forms": ListRequestFormsInput,
    "create_intake_request": CreateIntakeRequestInput,
    "get_intake_request": GetIntakeRequestInput,
    "list_my_requests": ListMyRequestsInput,
    "get_request_sla": GetRequestSlaInput,
    "get_request_documents": GetRequestDocumentsInput,
    "add_document": AddDocumentInput,
}


def _field_path(loc: tuple[Any, ...]) -> str:
    return ".".join(str(p) for p in loc)


def validation_to_tool_error(exc: ValidationError, model: type[BaseModel]) -> ToolError:
    """Flatten a pydantic error to 'field: message; …'. An unknown field gets the plan's
    sentence naming the field and the accepted ones. Input values are never echoed (they can
    be large base64 payloads)."""
    parts: list[str] = []
    first_field: str | None = None
    for err in exc.errors():
        loc = tuple(err.get("loc", ()))
        path = _field_path(loc)
        if first_field is None and path:
            first_field = path
        if err["type"] == "extra_forbidden":
            accepted_model = AttachmentInput if "attachments" in loc[:-1] else model
            accepted = ", ".join(sorted(accepted_model.model_fields))
            parts.append(
                f"Unknown input field '{loc[-1]}'. This tool accepts only: {accepted}."
            )
        elif err["type"] == "missing":
            parts.append(f"{path}: this field is required")
        else:
            parts.append(f"{path}: {err.get('msg', 'invalid value')}" if path else err["msg"])
    return ToolError("invalid_input", "; ".join(parts), field=first_field)
