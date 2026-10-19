"""Pure construction of sparse observations; no daily allocation or interpolation."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal

from .models import CHINA_TZ, ZERO, Bill


class HistoryConflict(Exception):
    """Source data is ambiguous; the reason contains no private values."""


@dataclass(frozen=True)
class Point:
    start: datetime
    state: Decimal
    total: Decimal

    @property
    def key(self) -> str:
        return self.start.isoformat()

    def values(self) -> list[str]:
        return [str(self.state), str(self.total)]


def day_start(day: date) -> datetime:
    """The source knows only the day, not the actual time of reading."""
    return datetime.combine(day, time.min, CHINA_TZ).astimezone(UTC)


def water_points(bills: tuple[Bill, ...]) -> tuple[Point, ...]:
    observations: dict[date, Decimal] = {}
    for bill in bills:
        if bill.end_reading - bill.start_reading != bill.volume:
            raise HistoryConflict("区间用量与起止读数不一致")
        for day, reading in ((bill.start, bill.start_reading), (bill.end, bill.end_reading)):
            if day in observations and observations[day] != reading:
                raise HistoryConflict("同一抄表日期存在相互矛盾的读数")
            observations[day] = reading
    if not observations:
        return ()
    ordered = sorted(observations.items())
    baseline = ordered[0][1]
    previous = baseline
    points = []
    for day, reading in ordered:
        if reading < previous:
            raise HistoryConflict("同一水表编号读数回退，需核实换表或数据修订")
        previous = reading
        points.append(Point(day_start(day), reading, reading - baseline))
    return tuple(points)


def cost_points(bills: tuple[Bill, ...]) -> tuple[Point, ...]:
    if not bills:
        return ()
    ordered = sorted(bills, key=lambda bill: (bill.end, bill.start, bill.key))
    seen_intervals = set()
    previous_end = None
    for bill in ordered:
        interval = (bill.start, bill.end)
        if interval in seen_intervals or (previous_end and bill.start < previous_end):
            raise HistoryConflict("重复或重叠账单无法确认是否补发，暂停应收费用统计")
        seen_intervals.add(interval)
        previous_end = bill.end
        if bill.fees and sum((fee.amount for fee in bill.fees), ZERO) != bill.amount:
            raise HistoryConflict("费用明细之和与应收金额不一致")
    total = ZERO
    points = [Point(day_start(ordered[0].start), ZERO, ZERO)]
    for bill in ordered:
        total += bill.amount
        points.append(Point(day_start(bill.end), total, total))
    return tuple(points)


def validate_extension(committed: dict[str, list[str]], points: tuple[Point, ...]) -> None:
    """Only append new observations; never silently rewrite committed history."""
    candidate = {point.key: point.values() for point in points}
    for stamp, values in committed.items():
        if stamp not in candidate or any(
            Decimal(a) != Decimal(b) for a, b in zip(values, candidate[stamp], strict=True)
        ):
            raise HistoryConflict("已同步历史被源站修订，保留原统计等待核查")
    if committed:
        last = max(committed)
        if any(stamp not in committed and stamp <= last for stamp in candidate):
            raise HistoryConflict("已同步范围内出现补发或新增历史端点，需核查")
