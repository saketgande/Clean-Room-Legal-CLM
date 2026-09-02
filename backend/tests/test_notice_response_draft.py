"""A generated reply to a legal notice fails dangerously, not clumsily — the
risk isn't awkward prose, it's an admission nobody authorised.

The LLM output is governed by the prompt, but the deterministic skeleton used
under mock mode / API failure is code, so it's pinned here: it must be a valid
holding reply that concedes nothing, and it must still carry the notice's own
particulars so the lawyer isn't retyping them.
"""

from datetime import date

from app.notices.drafting import _skeleton
from app.notices.models import Notice


def _notice(**kwargs) -> Notice:
    defaults = {
        "ref": "NOT-1001",
        "subject": "Demand for payment of outstanding invoices",
        "counterparty_name": "Northwind Traders GmbH",
        "counterparty_ref": "NT/LEG/2026/118",
        "notice_date": date(2026, 7, 12),
        "status": "open",
    }
    return Notice(**{**defaults, **kwargs})


def test_skeleton_concedes_nothing():
    """The property that matters: a holding reply must not accept liability,
    agree a sum, or commit to a remedy."""
    text = _skeleton(_notice()).lower()
    assert "admission of liability" in text  # explicitly disclaimed
    assert "rights and remedies are fully reserved" in text
    for forbidden in ("we agree to pay", "we accept", "we admit", "we will pay"):
        assert forbidden not in text


def test_skeleton_carries_the_notices_particulars():
    text = _skeleton(_notice())
    assert "Demand for payment of outstanding invoices" in text
    assert "NT/LEG/2026/118" in text          # their reference, not ours
    assert "2026-07-12" in text               # acknowledges the date served


def test_skeleton_falls_back_to_our_ref_when_they_gave_none():
    text = _skeleton(_notice(counterparty_ref=None))
    assert "NOT-1001" in text


def test_skeleton_omits_the_date_clause_when_undated():
    """An undated notice must not produce 'dated None'."""
    text = _skeleton(_notice(notice_date=None))
    assert "None" not in text
    assert "We acknowledge receipt of your notice." in text


def test_skeleton_leaves_signature_as_a_placeholder():
    # Never sign a letter on someone's behalf.
    text = _skeleton(_notice())
    assert "[Name]" in text and "[Title]" in text


def test_skeleton_is_a_complete_letter():
    text = _skeleton(_notice())
    assert text.startswith("Dear Sirs,")
    assert "Yours faithfully," in text
