"""Cumulative history is tied to actual meter endpoints, never bill-row sums."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from custom_components.handan_water.statistic_points import (
    HistoryConflict,
    cost_points,
    validate_extension,
    water_points,
)


def test_sparse_actual_readings_are_separate_and_exact(sample):
    results = {}
    for meter in {bill.meter for bill in sample.bills}:
        bills = tuple(bill for bill in sample.bills if bill.meter == meter)
        points = water_points(bills)
        assert len(points) == 7  # Six intervals plus the true initial observation.
        assert points[-1].start == datetime(2026, 9, 13, 16, tzinfo=UTC)
        assert points[-1].start != datetime(2026, 9, 1, tzinfo=UTC)
        assert points[0].total == 0
        assert len({point.key for point in points}) == 7
        assert (points[1].start - points[0].start).days > 30
        assert water_points(tuple(reversed(bills))) == points
        assert water_points(bills + bills) == points
        results[meter] = (points[-1].state, points[-1].total, cost_points(bills)[-1].total)
    assert results == {
        "DEMO_METER_001": (Decimal(1027), Decimal(27), Decimal("135")),
        "DEMO_METER_002": (Decimal(209), Decimal(9), Decimal("45")),
    }


def test_same_meter_reading_regression_and_same_day_conflict(sample):
    bills = tuple(bill for bill in sample.bills if bill.meter == "DEMO_METER_001")
    last = max(bills, key=lambda bill: bill.end)
    for broken in (
        replace(last, volume=last.volume + 1),
        replace(last, end_reading=Decimal(4), volume=last.volume),
        replace(last, start_reading=last.start_reading - 1, volume=last.volume + 1),
    ):
        with pytest.raises(HistoryConflict):
            water_points(tuple(bill for bill in bills if bill != last) + (broken,))


def test_new_meter_has_independent_baseline(sample):
    bill = replace(
        sample.bills[0], meter="NEW_METER", start_reading=Decimal(1), end_reading=Decimal(3)
    )
    assert water_points((bill,))[-1].total == 2


def test_duplicate_reissued_bill_never_double_counts_water_or_cost(sample):
    bill = sample.bills[0]
    reissued = replace(bill, key="another_bill_id")
    assert water_points((bill, reissued)) == water_points((bill,))
    with pytest.raises(HistoryConflict):
        cost_points((bill, reissued))


def test_cost_detail_is_verified_and_penalties_not_added(sample):
    bill = sample.bills[0]
    assert cost_points((replace(bill, penalty=Decimal(999)),))[-1].total == bill.amount
    with pytest.raises(HistoryConflict):
        cost_points((replace(bill, amount=bill.amount + 1),))


def test_late_corrections_and_earlier_baselines_freeze(sample):
    points = water_points(tuple(bill for bill in sample.bills if bill.meter == "DEMO_METER_001"))
    committed = {point.key: point.values() for point in points}
    validate_extension(committed, points)
    with pytest.raises(HistoryConflict):
        validate_extension(committed, points[:-1])
    with pytest.raises(HistoryConflict):
        validate_extension(committed, points[:-1] + (replace(points[-1], total=Decimal(999)),))
    inserted = replace(
        points[0], start=datetime(2025, 9, 1, tzinfo=UTC), state=Decimal(1001), total=Decimal(1)
    )
    with pytest.raises(HistoryConflict):
        validate_extension(committed, (points[0], inserted, *points[1:]))
    appended = replace(
        points[-1], start=datetime(2026, 12, 1, tzinfo=UTC), state=Decimal(1033), total=Decimal(33)
    )
    validate_extension(committed, points + (appended,))
