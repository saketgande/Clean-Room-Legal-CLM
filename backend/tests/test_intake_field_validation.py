"""A form's declared field kinds must actually be enforced.

A form field is declared `number`, `date`, `boolean` or `select` with a fixed
option list — and the only thing ever checked was required-ness. So a `number` field accepted "banana", a `date`
accepted "next Tuesday-ish", and a `select` accepted any string at all.
Everything reading those values back — arithmetic, date comparison, filtering
by option — was working on whatever the caller happened to send.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.intake.service import _validate_field_values


def _type(*fields) -> list:
    return list(fields)


def _field(key, kind="text", *, required=False, label=None, options=None):
    return SimpleNamespace(
        key=key, label=label or key.replace("_", " ").title(),
        kind=kind, required=required, options=options,
    )


def _clean(rtype, values):
    return _validate_field_values(rtype, values)


# --- number -----------------------------------------------------------------


def test_a_number_field_refuses_a_non_number():
    """The headline case. A contract value of "banana" flowed straight into
    the row and then into approval routing, which compares it against spend
    thresholds."""
    rtype = _type(_field("contract_value", "number"))
    with pytest.raises(HTTPException) as exc:
        _clean(rtype, {"contract_value": "banana"})
    assert exc.value.status_code == 422
    assert "Contract Value" in exc.value.detail


def test_a_numeric_string_is_coerced_not_rejected():
    """Guards over-correcting into a regression: HTML forms post everything as
    strings, so refusing "1500" would break every existing form."""
    rtype = _type(_field("contract_value", "number"))
    assert _clean(rtype, {"contract_value": "1500"})["contract_value"] == 1500
    assert _clean(rtype, {"contract_value": "1,500,000"})["contract_value"] == 1500000
    assert _clean(rtype, {"contract_value": "2500.75"})["contract_value"] == 2500.75
    assert _clean(rtype, {"contract_value": 900})["contract_value"] == 900


def test_a_boolean_is_not_a_number():
    """Guards a Python trap: `bool` is a subclass of `int`, so an isinstance
    check on int alone would silently store True as the contract value."""
    rtype = _type(_field("contract_value", "number"))
    with pytest.raises(HTTPException):
        _clean(rtype, {"contract_value": True})


# --- boolean ----------------------------------------------------------------


def test_boolean_accepts_the_shapes_a_form_actually_sends():
    rtype = _type(_field("nda_signed", "boolean"))
    for raw in (True, "true", "TRUE", "yes", "1", "on"):
        assert _clean(rtype, {"nda_signed": raw})["nda_signed"] is True
    for raw in (False, "false", "no", "0", "off"):
        assert _clean(rtype, {"nda_signed": raw})["nda_signed"] is False


def test_boolean_refuses_anything_else():
    rtype = _type(_field("nda_signed", "boolean"))
    with pytest.raises(HTTPException):
        _clean(rtype, {"nda_signed": "maybe"})


# --- date -------------------------------------------------------------------


def test_a_date_field_refuses_prose_and_stores_iso():
    """Renewal and notice deadlines are computed from these. A date that only
    looks like a date produces a deadline nobody can act on."""
    rtype = _type(_field("needed_by", "date"))
    assert _clean(rtype, {"needed_by": "2026-11-30"})["needed_by"] == "2026-11-30"
    with pytest.raises(HTTPException):
        _clean(rtype, {"needed_by": "next Tuesday-ish"})
    with pytest.raises(HTTPException):
        _clean(rtype, {"needed_by": "30/11/2026"})


def test_a_datetime_is_narrowed_to_its_date():
    rtype = _type(_field("needed_by", "date"))
    assert _clean(rtype, {"needed_by": "2026-11-30T09:00:00Z"})["needed_by"] == "2026-11-30"


# --- select -----------------------------------------------------------------


def test_select_refuses_a_value_outside_its_options():
    """The option list is what the admin configured; a value outside it makes
    every downstream filter and count on that field wrong."""
    rtype = _type(_field("risk_tier", "select", options=[
        {"value": "low", "label": "Low"},
        {"value": "high", "label": "High"},
    ]))
    assert _clean(rtype, {"risk_tier": "high"})["risk_tier"] == "high"
    with pytest.raises(HTTPException) as exc:
        _clean(rtype, {"risk_tier": "catastrophic"})
    assert "low, high" in exc.value.detail


def test_select_with_no_configured_options_accepts_free_text():
    """Guards breaking a half-configured type: a select whose options an admin
    hasn't filled in yet must not reject everything."""
    rtype = _type(_field("risk_tier", "select", options=None))
    assert _clean(rtype, {"risk_tier": "anything"})["risk_tier"] == "anything"


# --- required, blanks, and passthrough --------------------------------------


def test_required_is_still_enforced():
    rtype = _type(_field("counterparty", required=True))
    with pytest.raises(HTTPException):
        _clean(rtype, {})
    with pytest.raises(HTTPException):
        _clean(rtype, {"counterparty": "   "})


def test_a_blank_optional_field_is_dropped_not_stored_as_empty():
    """An empty string stored in a `number` field would fail the type check it
    skipped on the way in — the next read is where it would surface."""
    rtype = _type(_field("contract_value", "number"))
    assert "contract_value" not in _clean(rtype, {"contract_value": ""})


def test_channel_metadata_is_passed_through_untouched():
    """Guards email and Gmail ingestion: they store `channel_from`,
    `counterparty` and `gmail_thread_id` in the same column as the declared
    fields, so rejecting undeclared keys would break both channels."""
    rtype = _type(_field("contract_value", "number"))
    cleaned = _clean(rtype, {
        "contract_value": "500",
        "channel_from": "counsel@acme.example",
        "channel_sender_verified": False,
        "gmail_thread_id": "thread-1",
    })
    assert cleaned["contract_value"] == 500
    assert cleaned["channel_from"] == "counsel@acme.example"
    assert cleaned["channel_sender_verified"] is False
    assert cleaned["gmail_thread_id"] == "thread-1"


def test_an_untyped_request_is_left_alone():
    """Channel intake files with no request type at all."""
    values = {"channel_from": "someone@example.com"}
    assert _validate_field_values(None, values) == values


def test_a_pasted_contract_does_not_fit_in_a_text_field():
    rtype = _type(_field("notes", "text"))
    with pytest.raises(HTTPException):
        _clean(rtype, {"notes": "x" * 20_001})


# --- the update path, which previously wrote straight through ---------------


def test_update_validates_too(monkeypatch):
    """Guards the second hole in the same feature: `update_request` assigned
    `payload.field_values` directly, so a required field could be emptied and
    a typed one replaced with anything at all once the request was filed."""
    from types import SimpleNamespace

    from app.intake import agreement_forms, service

    request = SimpleNamespace(
        status="open", stage="new", priority="Medium", department=None,
        description="", work_status=None,
        field_values={"request_form": "new_agreement", "contract_value": 1500},
    )
    monkeypatch.setattr(agreement_forms, "form_fields",
                        lambda key: _type(_field("contract_value", "number", required=True)))

    monkeypatch.setattr(service, "get_request", lambda db, *, user, request_id: request)
    monkeypatch.setattr(service, "serialize_request", lambda db, r: {"ok": True})

    class _Db:
        def commit(self):
            pass

        def refresh(self, obj):
            pass

    payload = SimpleNamespace(
        field_values={"contract_value": "banana"}, stage=None, priority=None,
        department=None, description=None, work_status=None,
    )
    with pytest.raises(HTTPException) as exc:
        service.update_request(_Db(), actor=SimpleNamespace(id="u1", org_id="o1"),
                               request_id="r1", payload=payload)
    assert exc.value.status_code == 422

    # The stored value is untouched by the rejected update.
    assert request.field_values == {"request_form": "new_agreement", "contract_value": 1500}
