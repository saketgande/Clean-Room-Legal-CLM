"""The advertised tool set. Failure mode guarded: a stray or renamed tool widening what an
agent can do, or a schema that silently drops misspelt arguments (AC-1, AC-28)."""

from __future__ import annotations

from aegis_intake_mcp.schemas import TOOL_INPUT_MODELS
from aegis_intake_mcp.tools import TOOLS

EXPECTED = {
    "list_request_forms",
    "create_intake_request",
    "get_intake_request",
    "list_my_requests",
    "get_request_sla",
    "get_request_documents",
    "add_document",
}


def test_exactly_the_seven_tools():
    assert set(TOOLS) == EXPECTED
    assert len(TOOLS) == 7
    assert set(TOOL_INPUT_MODELS) == EXPECTED


def test_each_tool_has_description_and_closed_schema():
    for name, spec in TOOLS.items():
        assert spec.name == name
        assert spec.description.strip(), name
        schema = spec.input_schema()
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False, name


def test_attachment_items_are_closed_too():
    schema = TOOLS["create_intake_request"].input_schema()
    assert schema["$defs"]["AttachmentInput"]["additionalProperties"] is False
    assert "form_key" in schema["required"] and "answers" in schema["required"]


def test_write_tools_say_how_to_retry_safely():
    create = TOOLS["create_intake_request"].description
    assert "idempotency_key" in create and "never URLs or file paths" in create
    assert "Not idempotent" in TOOLS["add_document"].description
