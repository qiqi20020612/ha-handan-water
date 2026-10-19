"""Chinese account entities and independently identified water meters."""

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.core import callback

from .const import CONF_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT
from .entity import HandanWaterEntity
from .models import Bill
from .statistic_points import HistoryConflict, cost_points, water_points


@dataclass(frozen=True, kw_only=True)
class SensorSpec:
    key: str
    name: str
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    enabled: bool = True
    diagnostic: bool = False


ACCOUNT_SENSORS = (
    SensorSpec(key="owed", name="账户欠费", unit="CNY", device_class=SensorDeviceClass.MONETARY),
    SensorSpec(key="prepaid", name="预存余额", unit="CNY", device_class=SensorDeviceClass.MONETARY),
    SensorSpec(
        key="recent_payment",
        name="最近缴费金额",
        unit="CNY",
        device_class=SensorDeviceClass.MONETARY,
    ),
    SensorSpec(
        key="last_settled",
        name="最近缴费应收金额",
        unit="CNY",
        device_class=SensorDeviceClass.MONETARY,
        enabled=False,
    ),
    SensorSpec(
        key="last_deduction",
        name="最近预存款抵扣",
        unit="CNY",
        device_class=SensorDeviceClass.MONETARY,
        enabled=False,
    ),
    SensorSpec(
        key="last_prepaid_change",
        name="最近预存款变动",
        unit="CNY",
        device_class=SensorDeviceClass.MONETARY,
        enabled=False,
    ),
    SensorSpec(
        key="payment_history", name="历史缴费记录", unit="笔", enabled=False, diagnostic=True
    ),
    SensorSpec(key="analysis", name="网站用水分析", enabled=False, diagnostic=True),
    SensorSpec(key="sync_status", name="历史同步状态", diagnostic=True),
    SensorSpec(
        key="last_update",
        name="最近成功刷新",
        device_class=SensorDeviceClass.TIMESTAMP,
        enabled=False,
        diagnostic=True,
    ),
)
METER_SENSORS = (
    SensorSpec(
        key="reading",
        name="累计水表读数",
        unit=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorSpec(key="period_volume", name="本期用水量", unit=UnitOfVolume.CUBIC_METERS),
    SensorSpec(
        key="period_cost",
        name="本期账单应收金额",
        unit="CNY",
        device_class=SensorDeviceClass.MONETARY,
    ),
    SensorSpec(key="reading_date", name="最近抄表日期", device_class=SensorDeviceClass.DATE),
    SensorSpec(key="bill_history", name="历史账单", unit="张", diagnostic=True),
    SensorSpec(
        key="cumulative_cost",
        name="已知账单累计应收",
        unit="CNY",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        enabled=False,
        diagnostic=True,
    ),
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    async_add_entities(HandanWaterSensor(coordinator, spec) for spec in ACCOUNT_SENSORS)
    known: set[str] = set()

    @callback
    def discover() -> None:
        # Also restore identities of meters outside the current rolling window.
        new = coordinator.history.meters - known
        if new:
            known.update(new)
            async_add_entities(
                HandanWaterSensor(coordinator, spec, meter)
                for meter in sorted(new)
                for spec in METER_SENSORS
            )

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


def bill_attributes(bill: Bill) -> dict[str, Any]:
    return {
        "账期": bill.month,
        "起始抄表日期": bill.start.isoformat(),
        "结束抄表日期": bill.end.isoformat(),
        "起始读数": str(bill.start_reading),
        "结束读数": str(bill.end_reading),
        "区间用水量": str(bill.volume),
        "账单应收金额": str(bill.amount),
        "滞纳金字段": None if bill.penalty is None else str(bill.penalty),
        "缴清标记": bill.paid,
        "费用明细": [
            {
                "项目": fee.name,
                "单价": str(fee.price),
                "水量": str(fee.volume),
                "金额": str(fee.amount),
            }
            for fee in bill.fees
        ],
    }


def bounded_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Leave room for fixed attributes within Recorder's 16 KiB limit."""
    selected = []
    size = 2
    for record in records:
        length = len(json.dumps(record, ensure_ascii=False).encode()) + 1
        if size + length > 11000:
            break
        selected.append(record)
        size += length
    return selected


class HandanWaterSensor(HandanWaterEntity, SensorEntity):
    def __init__(self, coordinator, spec: SensorSpec, meter: str | None = None) -> None:
        super().__init__(coordinator, spec.key, spec.name, meter)
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_device_class = spec.device_class
        self._attr_state_class = spec.state_class
        self._attr_entity_registry_enabled_default = spec.enabled
        self._attr_entity_category = EntityCategory.DIAGNOSTIC if spec.diagnostic else None
        self._attr_suggested_display_precision = 2 if spec.unit in {"CNY", "m³"} else None

    @property
    def current_bill(self) -> Bill | None:
        bills = [bill for bill in self.coordinator.data.bills if bill.meter == self.meter]
        if not bills:
            return None
        latest_date = max(bill.end for bill in bills)
        latest = [bill for bill in bills if bill.end == latest_date]
        # Multiple bills for the same latest date must be inspected in history.
        return latest[0] if len(latest) == 1 else None

    @property
    def available(self) -> bool:
        if self.key in {"sync_status", "last_update"}:
            return True
        if not super().available:
            return False
        if self.key in {"last_settled", "last_deduction", "last_prepaid_change", "payment_history"}:
            return self.coordinator.data.payments is not None
        if self.key == "analysis":
            return self.coordinator.data.analysis is not None
        if self.key == "reading":
            return self.coordinator.history.water_issue(self.meter) is None
        return True

    @property
    def native_value(self) -> Decimal | str | int | Any | None:
        snapshot = self.coordinator.data
        history = self.coordinator.history
        if self.key in {"owed", "prepaid", "recent_payment"}:
            return getattr(snapshot.account, self.key)
        if self.key == "sync_status":
            return history.status
        if self.key == "last_update":
            return snapshot.fetched_at
        if self.key in {"last_settled", "last_deduction", "last_prepaid_change"}:
            payment = snapshot.payments[-1] if snapshot.payments else None
            if payment is None:
                return None
            return getattr(
                payment,
                {
                    "last_settled": "settled",
                    "last_deduction": "deduction",
                    "last_prepaid_change": "prepaid_change",
                }[self.key],
            )
        if self.key == "payment_history":
            return len(history.payments)
        if self.key == "analysis":
            return "账户与水表归属未明确" if snapshot.analysis else "暂无数据"
        bills = history.meter_bills(self.meter)
        if self.key == "bill_history":
            return len(bills)
        if self.key in {"reading", "cumulative_cost"}:
            if self.key == "cumulative_cost" and history.issues.get(
                history.statistic_id(self.meter, "cost")
            ):
                return None
            try:
                points = (water_points if self.key == "reading" else cost_points)(bills)
            except HistoryConflict:
                return None
            return points[-1].state if points else None
        bill = self.current_bill
        if bill is None:
            return None
        return {
            "period_volume": bill.volume,
            "period_cost": bill.amount,
            "reading_date": bill.end,
        }.get(self.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        history = self.coordinator.history
        snapshot = self.coordinator.data
        limit = self.coordinator.options.get(CONF_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT)
        if self.key == "sync_status":
            return {
                "最近完成同步": history.last_sync.isoformat() if history.last_sync else None,
                "统计问题": history.issues,
                "抄表修订问题数量": len(history.source_blocks),
                "传输方式": self.coordinator.client.transport,
                "缴费查询状态": snapshot.payment_error
                or ("成功" if snapshot.payments is not None else "已关闭"),
                "网站分析状态": snapshot.analysis_error
                or ("成功" if snapshot.analysis is not None else "已关闭"),
            }
        if self.key == "recent_payment":
            return {"来源": "200001 RecentPay；账户级最近缴费，不是账单应收"}
        if self.key in {"last_settled", "last_deduction", "last_prepaid_change"}:
            payment = snapshot.payments[-1] if snapshot.payments else None
            return {
                "缴费时间": payment.when.isoformat() if payment else None,
                "归属": "账户级；不分摊至水表",
            }
        if self.key == "payment_history":
            payments = sorted(
                history.payments.values(),
                key=lambda payment: (payment.when, payment.key),
                reverse=True,
            )
            return {
                "缴费记录": bounded_records(
                    [
                        {
                            "缴费时间": payment.when.isoformat(),
                            "账期": payment.month,
                            "实缴金额": str(payment.paid),
                            "应交销账金额": str(payment.settled),
                            "期初预存": str(payment.prepaid_before),
                            "预存变动": str(payment.prepaid_change),
                            "期末预存": str(payment.prepaid_after),
                            "抵扣预存": str(payment.deduction),
                            "滞纳金字段": None if payment.penalty is None else str(payment.penalty),
                        }
                        for payment in payments[:limit]
                    ]
                ),
                "属性显示上限": limit,
                "归属": "账户级交易，内嵌销账明细无水表编号，不能分摊",
            }
        if self.key == "analysis":
            return {
                "网站账期趋势": [
                    {"账期": month, "用量": str(volume)}
                    for month, volume in (snapshot.analysis or ())
                ],
                "统计用途": "仅诊断；不能作为多表总量",
            }
        if self.meter is None:
            return {}
        attributes = {
            "水表编号": self.meter,
            "数据来源": "账单实际抄表端点，非实时水表",
            "时间精度": "日期；小时和每日实际用量未知",
            "历史水量统计ID": history.statistic_id(self.meter, "water"),
            "历史应收统计ID": history.statistic_id(self.meter, "cost"),
        }
        if issue := history.water_issue(self.meter):
            attributes["读数统计暂停原因"] = issue
        if self.current_bill:
            attributes.update(bill_attributes(self.current_bill))
        if self.key == "bill_history":
            bills = history.meter_bills(self.meter)
            attributes["账单记录"] = bounded_records(
                [bill_attributes(bill) for bill in reversed(bills[-limit:])]
            )
            attributes["属性显示上限"] = limit
        return attributes
