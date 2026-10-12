"""Exact money handling. Contract values and authority limits are NUMERIC(18,2);
compare amounts as Decimal, never float (0.1 + 0.2 != 0.3 in binary floating point)."""

from decimal import Decimal


def to_money(value) -> Decimal:
    """Decimal for a stored or supplied amount; None or blank counts as 0.
    Floats go through str() so 10000.1 becomes Decimal("10000.1"), not its binary expansion."""
    if value is None or value == "":
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
