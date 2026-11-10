"""FR-3: the payment-terms choices depend on the agreement type (spec 014).

Pure validator tests, no database: they call the same functions filing and updating use.
"""

import pytest
from fastapi import HTTPException

from app.intake.agreement_forms import form_fields, options_for
from app.intake.service import _validate_field_values

_FIELDS = form_fields("new_agreement")
_PAY = next(f for f in _FIELDS if f.key == "payment_terms")


def _check(agreement_type, days, stored=None):
    return _validate_field_values(
        [_PAY], {"agreement_type": agreement_type, "payment_terms": days}, stored=stored
    )


@pytest.mark.parametrize("kind", ["Services (MSA)", "Consultancy"])
def test_msa_and_consultancy_offer_only_45_and_60(kind):
    """The playbook rejects 30 and 90 days for these types; offering them files a request that
    can only be redlined."""
    assert _check(kind, "45 days")["payment_terms"] == "45 days"
    assert _check(kind, "60 days")["payment_terms"] == "60 days"
    for bad in ("30 days", "90 days"):
        with pytest.raises(HTTPException) as exc:
            _check(kind, bad)
        assert exc.value.status_code == 422
        assert "must be one of: 45 days, 60 days" in exc.value.detail


@pytest.mark.parametrize("kind", ["Buying from a vendor", "Software or SaaS"])
def test_vendor_and_saas_offer_45_60_90(kind):
    """Vendor and SaaS paper allows 90 days; 30 is the one dropped."""
    for ok in ("45 days", "60 days", "90 days"):
        assert _check(kind, ok)["payment_terms"] == ok
    with pytest.raises(HTTPException):
        _check(kind, "30 days")


@pytest.mark.parametrize("kind", ["Selling to a customer", "Something else"])
def test_other_types_keep_the_full_list(kind):
    """No template exists for these (FR-7), so narrowing them would only remove choices."""
    assert _check(kind, "30 days")["payment_terms"] == "30 days"


def test_a_filed_30_days_stays_editable_when_unchanged():
    """A request filed before the change still holds "30 days"; an unrelated edit sending it back
    as it was must not fail."""
    out = _check("Services (MSA)", "30 days", stored={"payment_terms": "30 days"})
    assert out["payment_terms"] == "30 days"
    with pytest.raises(HTTPException):  # but changing to another removed option is still refused
        _check("Services (MSA)", "90 days", stored={"payment_terms": "30 days"})


def test_options_for_without_options_when_is_options():
    """A question with no options_when behaves exactly as before."""
    f = next(x for x in _FIELDS if x.key == "agreement_type")
    assert options_for(f, {"agreement_type": "NDA"}) == f.options
