"""DB-01: money is stored and compared as exact decimals, so decisions at exactly
a limit are consistent and totals don't drift (50000.000000000007)."""

from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import Numeric

import app.models  # noqa: F401  (register every mapper)
from app.authority.models import AuthorityGrant
from app.authority.service import _grant_covers
from app.contracts.models import Contract
from app.core.money import to_money


def test_money_columns_are_exact_numeric():
    for column in (Contract.__table__.c.value_amount, AuthorityGrant.__table__.c.max_value):
        assert isinstance(column.type, Numeric)
        assert (column.type.precision, column.type.scale) == (18, 2)


def _grant(limit):
    return SimpleNamespace(max_value=limit, currency="USD", allowed_contract_types=[],
                           allowed_jurisdictions=[], max_risk_band=None)


def _contract(value):
    return SimpleNamespace(value_amount=value, currency="USD", contract_type="MSA",
                           jurisdiction="Delaware", risk_band=None, risk_level=None)


def test_a_value_exactly_at_the_authority_limit_is_covered():
    limit = Decimal("10000.10")
    assert _grant_covers(_grant(limit), _contract(Decimal("10000.10")))[0] is True
    assert _grant_covers(_grant(limit), _contract(10000.1))[0] is True  # intake values arrive as float
    assert _grant_covers(_grant(limit), _contract(Decimal("10000.11")))[0] is False


def test_totals_are_exact():
    assert sum(to_money(v) for v in (0.1, 0.2)) == Decimal("0.3")
