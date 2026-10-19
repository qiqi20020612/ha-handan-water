"""Persistent ledger and recoverable, idempotent Recorder imports."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .models import Bill, InvalidData, Payment, Snapshot, meter_identity
from .statistic_points import HistoryConflict, Point, cost_points, validate_extension, water_points


class History:
    def __init__(self, hass: HomeAssistant, entry_id: str, account_key: str) -> None:
        self.hass = hass
        self.account_key = account_key
        self.store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}", private=True
        )
        self.bills: dict[str, Bill] = {}
        self.payments: dict[str, Payment] = {}
        self.committed: dict[str, dict[str, list[str]]] = {}
        self.source_blocks: dict[str, str] = {}
        self.issues: dict[str, str] = {}
        # Runtime integrity conflicts gate readings; transient sync errors are
        # diagnostic only. Neither changes the persistent ledger/checkpoints.
        self._conflicts: dict[str, str] = {}
        self.status = "等待同步"
        self.last_sync: datetime | None = None
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        try:
            data = await self.store.async_load()
            if data is None:
                return
            if data["account_key"] != self.account_key:
                raise InvalidData("本地历史账本不属于此账户")
            self.bills = {key: Bill.restore(row) for key, row in data["bills"].items()}
            self.payments = {key: Payment.restore(row) for key, row in data["payments"].items()}
            self.committed = data["committed"]
            self.source_blocks = data["source_blocks"]
            self.last_sync = (
                datetime.fromisoformat(data["last_sync"]) if data.get("last_sync") else None
            )
            # Validate checkpoints before considering any import.
            for series in self.committed.values():
                for stamp, values in series.items():
                    when = datetime.fromisoformat(stamp)
                    if (
                        when.tzinfo is None
                        or len(values) != 2
                        or any(not Decimal(value).is_finite() for value in values)
                    ):
                        raise ValueError
        except Exception:
            # Do not overwrite a corrupt/mismatched ledger or expose its contents.
            raise InvalidData("本地历史账本无法安全读取，请保留文件并核查") from None

    async def async_save(self) -> None:
        await self.store.async_save(
            {
                "account_key": self.account_key,
                "bills": {key: bill.serialize() for key, bill in self.bills.items()},
                "payments": {key: payment.serialize() for key, payment in self.payments.items()},
                "committed": self.committed,
                "source_blocks": self.source_blocks,
                "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            }
        )

    def meter_bills(self, meter: str) -> tuple[Bill, ...]:
        return tuple(
            sorted(
                (bill for bill in self.bills.values() if bill.meter == meter),
                key=lambda bill: (bill.end, bill.start, bill.key),
            )
        )

    @property
    def meters(self) -> set[str]:
        return {bill.meter for bill in self.bills.values()}

    def statistic_id(self, meter: str, kind: str) -> str:
        return f"{DOMAIN}:{meter_identity(self.account_key, meter)}_{kind}"

    def water_issue(self, meter: str) -> str | None:
        if meter in self.source_blocks:
            return self.source_blocks[meter]
        try:
            points = water_points(self.meter_bills(meter))
            validate_extension(self.committed.get(self.statistic_id(meter, "water"), {}), points)
        except HistoryConflict as err:
            return str(err)
        return self._conflicts.get(self.statistic_id(meter, "water"))

    async def async_merge(self, snapshot: Snapshot) -> None:
        async with self._lock:
            for bill in snapshot.bills:
                old = self.bills.get(bill.key)
                if old and old.water_signature != bill.water_signature:
                    for meter in (old.meter, bill.meter):
                        self.source_blocks[meter] = "已有账单的抄表数据被修改，需核查后恢复"
                self.bills[bill.key] = bill
            if snapshot.payments is not None:
                for payment in snapshot.payments:
                    self.payments[payment.key] = payment
            # Persist source observations before touching Recorder. This is the WAL.
            await self.async_save()

    async def _async_read(self, statistic_id: str) -> dict[str, list[str]]:
        recorder = get_instance(self.hass)
        result = await recorder.async_add_executor_job(
            partial(
                statistics_during_period,
                self.hass,
                datetime(1970, 1, 1, tzinfo=UTC),
                datetime.now(UTC) + timedelta(hours=1),
                {statistic_id},
                "hour",
                None,
                {"state", "sum"},
            )
        )
        return {
            datetime.fromtimestamp(row["start"], UTC).isoformat(): [
                str(row["state"]),
                str(row["sum"]),
            ]
            for row in result.get(statistic_id, [])
        }

    @staticmethod
    def _match(actual: list[str], expected: list[str]) -> bool:
        try:
            return len(actual) == len(expected) == 2 and all(
                abs(Decimal(a) - Decimal(b)) < Decimal("0.000001")
                for a, b in zip(actual, expected, strict=True)
            )
        except Exception:
            return False

    async def _async_import(self, meter: str, kind: str, points: tuple[Point, ...]) -> None:
        statistic_id = self.statistic_id(meter, kind)
        validate_extension(self.committed.get(statistic_id, {}), points)
        expected = {point.key: point.values() for point in points}
        actual = await self._async_read(statistic_id)
        # Catch manual DB edits, a missing Store baseline, or an interrupted old plan.
        if any(
            key not in expected or not self._match(values, expected[key])
            for key, values in actual.items()
        ):
            raise HistoryConflict("Recorder 与本地账本不一致，暂停覆盖已有历史")
        missing = [point for point in points if point.key not in actual]
        if missing:
            metadata = StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=f"邯郸供水 水表 {meter} {'历史累计抄表观测' if kind == 'water' else '历史累计账单应收'}",
                source=DOMAIN,
                statistic_id=statistic_id,
                unit_class="volume" if kind == "water" else None,
                unit_of_measurement="m³" if kind == "water" else "CNY",
            )
            stats = [
                StatisticData(start=point.start, state=float(point.state), sum=float(point.total))
                for point in missing
            ]
            async_add_external_statistics(self.hass, metadata, stats)
            # The API only queues work. Never mark a queued job as a completed import.
            await get_instance(self.hass).async_block_till_done()
        # A worker may have dequeued an import when the commit helper checks
        # queue.empty(). Readback is the authority, including that short race.
        for attempt in range(6):
            verified = await self._async_read(statistic_id)
            if set(verified) == set(expected) and all(
                self._match(verified[key], values) for key, values in expected.items()
            ):
                break
            if attempt == 5:
                raise InvalidData("历史统计写入尚未确认，可重试恢复同步")
            await asyncio.sleep(0.1 * (attempt + 1))
        self.committed[statistic_id] = expected
        await self.async_save()

    async def async_sync(self, enabled: bool) -> None:
        async with self._lock:
            if not enabled:
                self.status = "历史导入已关闭"
                return
            if "recorder" not in self.hass.config.components:
                self.status = "等待 Recorder 启用"
                return
            # is_running also includes CoreState.starting, while Recorder's
            # worker may still be waiting for the startup event at that point.
            if self.hass.state is not CoreState.running:
                self.status = "等待 Home Assistant 启动"
                return
            self.status = "正在同步"
            self.issues = {}
            self._conflicts = {}
            had_failure = False
            for meter in sorted(self.meters):
                for kind, builder in (("water", water_points), ("cost", cost_points)):
                    statistic_id = self.statistic_id(meter, kind)
                    try:
                        if meter in self.source_blocks:
                            raise HistoryConflict(self.source_blocks[meter])
                        if kind == "cost":
                            # Inconsistent reading dates also make costs unsafe.
                            water_points(self.meter_bills(meter))
                        points = builder(self.meter_bills(meter))
                        if points:
                            # Recorder failure must not indefinitely block source
                            # updates or hold the per-account synchronization lock.
                            async with asyncio.timeout(30):
                                await self._async_import(meter, kind, points)
                    except HistoryConflict as err:
                        self.issues[statistic_id] = str(err)
                        self._conflicts[statistic_id] = str(err)
                    except Exception:
                        had_failure = True
                        self.issues[statistic_id] = "历史统计同步失败，可重试恢复同步"
            self.status = (
                "同步失败，可重试"
                if had_failure
                else (
                    "部分统计暂停，需核查"
                    if self.issues
                    else ("同步完成" if self.meters else "暂无历史账单")
                )
            )
            if not self.issues:
                self.last_sync = datetime.now(UTC)
            await self.async_save()
