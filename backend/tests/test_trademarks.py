"""Tests for the trademarks module. Matches the repository's existing
pure-function / lightweight-stub test style (no live DB or TestClient
harness is configured for this suite) - see test_sprint1_criticals.py.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.enums import SourceStatus, TrademarkRiskLevel
from app.core.rbac import (
    ADMIN_ROLE_NAME,
    ALL_PERMISSIONS,
    DEFAULT_ROLE_PERMISSIONS,
    LEGAL_REVIEWER_ROLE_NAME,
    MEMBER_ROLE_NAME,
    TRADEMARK_PERMISSIONS,
)
from app.trademarks.extraction.field_capture import apply_field_definitions
from app.trademarks.providers.similarity import _build_summary, _risk_level
from app.trademarks.schemas import (
    ExtractedRecord,
    FieldDefinition,
    SearchSimilarRequest,
    SourceResult,
)
from app.trademarks.service import _record_to_trademark_fields


def _settings(**overrides):
    defaults = {"trademark_risk_threshold_high": 0.85, "trademark_risk_threshold_medium": 0.60}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- RBAC wiring -------------------------------------------------------------


def test_trademark_permissions_are_registered_in_all_permissions():
    assert TRADEMARK_PERMISSIONS <= ALL_PERMISSIONS


def test_admin_role_has_every_trademark_permission():
    assert TRADEMARK_PERMISSIONS <= DEFAULT_ROLE_PERMISSIONS[ADMIN_ROLE_NAME]


def test_member_role_can_read_and_create_but_not_search_or_extract():
    member_perms = DEFAULT_ROLE_PERMISSIONS[MEMBER_ROLE_NAME]
    assert "trademark:read" in member_perms
    assert "trademark:create" in member_perms
    assert "trademark:search" not in member_perms
    assert "trademark:extract" not in member_perms
    assert "trademark:integrations_manage" not in member_perms


def test_legal_reviewer_role_has_search_and_extract():
    reviewer_perms = DEFAULT_ROLE_PERMISSIONS[LEGAL_REVIEWER_ROLE_NAME]
    assert TRADEMARK_PERMISSIONS <= reviewer_perms


# --- similarity search risk banding / summary --------------------------------


def test_risk_level_thresholds():
    settings = _settings()
    assert _risk_level(0.9, settings) == TrademarkRiskLevel.HIGH
    assert _risk_level(0.85, settings) == TrademarkRiskLevel.HIGH
    assert _risk_level(0.7, settings) == TrademarkRiskLevel.MEDIUM
    assert _risk_level(0.6, settings) == TrademarkRiskLevel.MEDIUM
    assert _risk_level(0.1, settings) == TrademarkRiskLevel.LOW


def test_build_summary_recommends_proceed_with_caution_on_any_high_risk():
    sources = {
        "internal_portfolio": SourceResult(
            status=SourceStatus.COMPLETE,
            results=[{"risk_level": TrademarkRiskLevel.HIGH}, {"risk_level": TrademarkRiskLevel.LOW}],
        ),
        "signa": SourceResult(status=SourceStatus.NOT_CONFIGURED, results=[]),
    }
    summary = _build_summary(sources)
    assert summary.high_risk_count == 1
    assert summary.low_risk_count == 1
    assert summary.recommendation == "proceed_with_caution"


def test_build_summary_clear_to_proceed_when_no_risk_hits():
    sources = {"web_search": SourceResult(status=SourceStatus.COMPLETE, results=[])}
    summary = _build_summary(sources)
    assert summary.recommendation == "clear_to_proceed"


def test_search_similar_request_defaults_to_india_jurisdiction():
    request = SearchSimilarRequest(trademark_name="PHARMOZAC")
    assert request.jurisdictions == ["IN"]
    assert request.trademark_type == "word_mark"


# --- generic-template field capture (pure text in/dict out) ------------------


def test_apply_field_definitions_captures_between_anchors():
    text = "Applicant Name: Acme Corp\nFiling Date: 12/05/2024\nStatus: Pending"
    fields = [
        FieldDefinition(name="applicant", data_type="string", order=0, anchor="Applicant Name:"),
        FieldDefinition(name="filing_date", data_type="string", order=1, anchor="Filing Date:"),
    ]
    result = apply_field_definitions(text, fields)
    assert result["applicant"] == "Acme Corp"
    assert result["filing_date"] == "12/05/2024"


def test_apply_field_definitions_regex_override_takes_precedence():
    text = "TM No: TM-2024-00123 filed"
    fields = [
        FieldDefinition(
            name="tm_number", data_type="string", order=0, anchor="TM No:", regex_override=r"(TM-\d+-\d+)"
        )
    ]
    result = apply_field_definitions(text, fields)
    assert result["tm_number"] == "TM-2024-00123"


def test_apply_field_definitions_coerces_number_and_boolean():
    text = "Class: 5\nRenewable: yes"
    fields = [
        FieldDefinition(name="nice_class", data_type="number", order=0, anchor="Class:"),
        FieldDefinition(name="renewable", data_type="boolean", order=1, anchor="Renewable:"),
    ]
    result = apply_field_definitions(text, fields)
    assert result["nice_class"] == 5
    assert result["renewable"] is True


def test_apply_field_definitions_missing_anchor_yields_none():
    fields = [FieldDefinition(name="missing", data_type="string", order=0, anchor="Nonexistent:")]
    result = apply_field_definitions("Some unrelated text", fields)
    assert result["missing"] is None


# --- ingest field mapping (pure function, no DB) ------------------------------


def test_record_to_trademark_fields_maps_ip_india_journal_entry():
    record = ExtractedRecord(
        page_number=1,
        fields={
            "product_name": "ZOLPHERA",
            "jurisdiction": "MUMBAI",
            "goods_services": "Pharmaceutical preparations",
            "tm_date": "12/05/2024",
            "address": "Acme Pharma Pvt Ltd, Mumbai",
        },
    )
    mapped = _record_to_trademark_fields(record, "ip_india_journal")
    assert mapped["name"] == "ZOLPHERA"
    assert mapped["jurisdiction"] == "MUMBAI"
    assert mapped["filed_on"].year == 2024
    assert mapped["filed_on"].month == 5
    assert mapped["filed_on"].day == 12


def test_record_to_trademark_fields_returns_none_without_a_name():
    record = ExtractedRecord(page_number=1, fields={"jurisdiction": "DELHI"})
    assert _record_to_trademark_fields(record, "ip_india_journal") is None


def test_record_to_trademark_fields_generic_template_best_effort_name_lookup():
    record = ExtractedRecord(page_number=1, fields={"trademark": "ACME", "jurisdiction": "IN"})
    mapped = _record_to_trademark_fields(record, "generic")
    assert mapped["name"] == "ACME"
