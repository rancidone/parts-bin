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


@pytest.mark.parametrize('stored,requested,comparison,expected', [
    ('0 V', '1 V', 'gte', '1'), ('1 V', '0 V', 'lte', '1'),
    ('0 V', '0 V', 'eq', '0'), ('0.9 V', '1000 mV', 'gte', '0.1'),
    ('900 mA', '1 A', 'gte', '0.1'), ('1 to 2 V', '3 V', 'gte', '0.3333333333333333333333333333'),
])
def test_relative_shortfall_has_consistent_units_and_handles_zero(stored, requested, comparison, expected):
    from domain.quantities import relative_interval_gap
    unit = 'A' if 'A' in stored else 'V'
    gap = relative_interval_gap(interval(stored, unit), interval(requested, unit), comparison)
    assert abs(gap - Decimal(expected)) < Decimal('1e-27')
