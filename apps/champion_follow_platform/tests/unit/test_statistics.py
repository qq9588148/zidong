from decimal import Decimal

from champion_follow.domain.statistics import (
    BREAK_EVEN_RATE,
    conservative_unit_return,
    fixed_unit_return,
    wilson_lower,
)


def test_wilson_lower_uses_the_frozen_one_sided_z_value():
    assert wilson_lower(20, 30) == Decimal("0.516595491454")
    assert wilson_lower(30, 30) == Decimal("0.917275691875")
    assert wilson_lower(0, 0) == Decimal("0")


def test_fixed_unit_return_and_break_even_are_exact_decimal_values():
    assert fixed_unit_return(51, 49) == Decimal("-0.0004")
    assert conservative_unit_return(Decimal("0.6")) == Decimal("0.176")
    assert BREAK_EVEN_RATE == Decimal("0.5102040816326530612244897959")


def test_fixed_return_uses_postgres_numeric_half_away_from_zero_rounding():
    assert fixed_unit_return(3, 8189) == Decimal("-0.999282226563")
