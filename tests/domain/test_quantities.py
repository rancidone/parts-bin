"""Shared numeric dimensions work independently of inventory categories."""

from decimal import Decimal

import pytest

from domain.quantities import compare_intervals, interval


@pytest.mark.parametrize('unit', ['V', 'A', 'Ω', 'W', 'F', 'H', 'VA', 'Hz', 'V/s', 'm', 'cd', 'ratio', '%'])
def test_interval_and_tolerance_share_dimensions(unit):
    assert interval(f'1 to 2 {unit}', unit) == (Decimal(1), Decimal(2))
    assert interval(f'1.5 {unit} ± 0.5 {unit}', unit) == (Decimal(1), Decimal(2))
    assert compare_intervals(interval(f'1 to 2 {unit}', unit), interval(f'1.5 {unit}', unit), 'gte') is None


def test_interval_comparison_requires_a_supported_operator():
    from domain import DomainError
    with pytest.raises(DomainError):
        compare_intervals((Decimal(1), Decimal(2)), (Decimal(1), Decimal(2)), 'contains')
