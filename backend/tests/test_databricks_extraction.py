"""Databricks extraction: response parsing, without a workspace.

These run today with no credentials — they stub the two network calls and feed
in the exact JSON shapes ai_parse_document and ai_extract are documented to
return. That covers the part we own (turning their payload into our objects);
the smoke test in scripts/check_databricks.py covers the part they own.
"""

import json

import pytest

from app.core.config import settings
from app.integrations.databricks import (
    CONTRACT_SCHEMA,
    DatabricksClient,
    ExtractedFields,
    ParsedDocument,
)

# ---------------------------------------------------------------- fixtures ---

PARSE_ELEMENTS = [
    {"id": 0, "type": "title", "content": "MASTER SERVICES AGREEMENT", "confidence": 0.99,
     "bbox": [{"page_id": 0}]},
    {"id": 1, "type": "text", "content": "This Agreement is made between Acme Laboratories Limited and Contoso Consulting LLP.",
     "confidence": 0.97, "bbox": [{"page_id": 0}]},
    {"id": 2, "type": "section_header", "content": "9. Limitation of Liability", "confidence": 0.95,
     "bbox": [{"page_id": 13}]},
    {"id": 3, "type": "text", "content": "Aggregate liability shall not exceed twelve months of fees.",
     "confidence": 0.93, "bbox": [{"page_id": 13}]},
    {"id": 4, "type": "table", "content": "<table><tr><td>Year 1</td><td>INR 100000</td></tr></table>",
     "confidence": 0.88, "bbox": [{"page_id": 21}]},
]

EXTRACT_PAYLOAD = {
    "response": {
        "counterparty": {"value": "Contoso Consulting LLP", "confidence_score": 0.96, "citation_ids": [1]},
        "effective_date": {"value": "2026-09-01", "confidence_score": 0.91},
        "end_date": {"value": "2029-08-31", "confidence_score": 0.89},
        "total_value": {"value": 100000, "confidence_score": 0.55},          # low → review
        "auto_renews": {"value": False, "confidence_score": 0.82},
        "liability_cap": {"value": "12 months of fees", "confidence_score": 0.45},  # low → review
    },
    "error_message": None,
    "metadata": {"version": "2.1"},
}


@pytest.fixture
def client(monkeypatch):
    """A client that believes it is configured but never touches the network."""
    monkeypatch.setattr(settings, "databricks_host", "https://example.cloud.databricks.com")
    monkeypatch.setattr(settings, "databricks_token", "dapi-test")
    monkeypatch.setattr(settings, "databricks_warehouse_id", "wh-test")
    monkeypatch.setattr(settings, "mock_databricks", False)
    c = DatabricksClient()

    async def _no_upload(self, volume_path, content):  # noqa: ARG001
        return None

    monkeypatch.setattr(DatabricksClient, "_put_file", _no_upload)
    return c


def _rows_for_parse():
    text = "\n".join(e["content"] for e in PARSE_ELEMENTS)
    # third column is the page COUNT returned by size(...), not a last index
    return [[text, json.dumps(PARSE_ELEMENTS), "22", "[]"]]


# ------------------------------------------------------------------- tests ---

async def test_parse_returns_whole_document(client, monkeypatch):
    """Full text, every element, page count — not a summary."""
    async def fake_sql(self, statement):  # noqa: ARG001
        assert "ai_parse_document" in statement
        return _rows_for_parse()

    monkeypatch.setattr(DatabricksClient, "_sql", fake_sql)
    doc: ParsedDocument = await client.parse_document(filename="msa.pdf", content=b"%PDF")

    assert "MASTER SERVICES AGREEMENT" in doc.text
    assert "twelve months of fees" in doc.text          # page 13 content survived
    assert len(doc.elements) == len(PARSE_ELEMENTS)
    assert doc.page_count == 22                          # size(pages)
    assert doc.errors == []


async def test_parse_keeps_page_numbers_and_tables(client, monkeypatch):
    """Citations need pages; payment schedules need to stay tables."""
    async def fake_sql(self, statement):  # noqa: ARG001
        return _rows_for_parse()

    monkeypatch.setattr(DatabricksClient, "_sql", fake_sql)
    doc = await client.parse_document(filename="msa.pdf", content=b"%PDF")

    by_page = doc.text_by_page()
    assert "9. Limitation of Liability" in by_page[13]
    assert 0 in by_page and 21 in by_page

    tables = doc.tables()
    assert len(tables) == 1 and tables[0].startswith("<table>")


async def test_extract_maps_fields_and_flags_low_confidence(client, monkeypatch):
    """The wrapper is unpacked, and anything the model was unsure about is
    surfaced rather than written onto the contract silently."""
    async def fake_sql(self, statement):  # noqa: ARG001
        assert "ai_extract" in statement
        assert "'mode', 'precision'" in statement       # precision on by default
        return [[json.dumps(EXTRACT_PAYLOAD)]]

    monkeypatch.setattr(DatabricksClient, "_sql", fake_sql)
    result: ExtractedFields = await client.extract_fields(filename="msa.pdf", content=b"%PDF")

    assert result.error is None
    assert result.fields["counterparty"] == "Contoso Consulting LLP"
    assert result.fields["total_value"] == 100000
    assert result.fields["auto_renews"] is False
    assert result.citations["counterparty"] == [1]
    assert result.needs_review() == ["liability_cap", "total_value"]
    assert "counterparty" not in result.needs_review()


async def test_precision_mode_can_be_turned_off(client, monkeypatch):
    """Short paper does not need the expensive path."""
    seen = {}

    async def fake_sql(self, statement):  # noqa: ARG001
        seen["sql"] = statement
        return [[json.dumps(EXTRACT_PAYLOAD)]]

    monkeypatch.setattr(DatabricksClient, "_sql", fake_sql)
    await client.extract_fields(filename="nda.pdf", content=b"%PDF", precision=False)
    assert "'mode', 'precision'" not in seen["sql"]
    assert "enableConfidenceScores" in seen["sql"]


async def test_extract_surfaces_provider_error(client, monkeypatch):
    """A failed extraction returns an error, never half-filled fields."""
    async def fake_sql(self, statement):  # noqa: ARG001
        return [[json.dumps({"response": {}, "error_message": "document exceeded context"})]]

    monkeypatch.setattr(DatabricksClient, "_sql", fake_sql)
    result = await client.extract_fields(filename="huge.pdf", content=b"%PDF")

    assert result.error == "document exceeded context"
    assert result.fields == {}


async def test_disabled_client_is_inert(monkeypatch):
    """With no credentials Aegis must behave exactly as it does today."""
    monkeypatch.setattr(settings, "databricks_host", None)
    monkeypatch.setattr(settings, "databricks_token", None)
    monkeypatch.setattr(settings, "databricks_warehouse_id", None)
    c = DatabricksClient()

    assert c.enabled is False
    doc = await c.parse_document(filename="x.pdf", content=b"x")
    fields = await c.extract_fields(filename="x.pdf", content=b"x")
    assert doc.text == "" and doc.page_count == 0
    assert fields.fields == {} and fields.error == "databricks disabled"


async def test_ocr_seam_matches_reducto_shape(client, monkeypatch):
    """extract_text must stay swappable with ReductoClient at the call site."""
    async def fake_sql(self, statement):  # noqa: ARG001
        return _rows_for_parse()

    monkeypatch.setattr(DatabricksClient, "_sql", fake_sql)
    ocr = await client.extract_text(filename="msa.pdf", mime_type="application/pdf", content=b"%PDF")

    assert ocr.provider == "databricks"
    assert ocr.quality_score > 0
    assert "MASTER SERVICES AGREEMENT" in ocr.text
    assert ocr.metadata["page_count"] == 22
    assert ocr.metadata["table_count"] == 1


def test_contract_schema_is_usable():
    """Every field carries a description — the cheapest accuracy win in
    ai_extract — and stays inside the 256-field / 150-char limits."""
    assert len(CONTRACT_SCHEMA) <= 256
    for name, spec in CONTRACT_SCHEMA.items():
        assert len(name) <= 150
        assert spec["type"] in {"string", "number", "integer", "boolean"}
        if name not in {"currency"}:
            assert spec.get("description"), f"{name} has no description"
