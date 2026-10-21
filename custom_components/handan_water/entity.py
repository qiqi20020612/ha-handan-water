"""Stable devices and Chinese entity names."""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import HandanWaterCoordinator
from .models import meter_identity


def account_device_info(account_key: str) -> dr.DeviceInfo:
    """Share account metadata with registration before meter entity creation."""
    return dr.DeviceInfo(
        identifiers={(DOMAIN, account_key)},
        name=f"{NAME} 账户 {account_key[:8]}",
        manufacturer="邯郸供水",
        model="供水账户",
        entry_type=dr.DeviceEntryType.SERVICE,
    )


class HandanWaterEntity(CoordinatorEntity[HandanWaterCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: HandanWaterCoordinator, key: str, name: str, meter: str | None = None
    ) -> None:
        super().__init__(coordinator)
        self.meter = meter
        self.key = key
        self._attr_name = name
        account_key = coordinator.history.account_key
        device_key = meter_identity(account_key, meter) if meter else account_key
        self._attr_unique_id = f"{device_key}_{key}"
        if meter:
            self._attr_device_info = dr.DeviceInfo(
                identifiers={(DOMAIN, device_key)},
                name=f"{NAME} 水表 {meter}",
                manufacturer="邯郸供水",
                model="供水水表（账单抄表）",
                via_device_id=dr.async_get_device_id_by_identifier(
                    coordinator.hass,
                    (DOMAIN, account_key),
                    config_entry_id=coordinator.config_entry.entry_id,
                ),
                serial_number=meter,
            )
        else:
            self._attr_device_info = account_device_info(account_key)

    @property
    def available(self) -> bool:
        return super().available and (
            self.meter is None or self.meter in self.coordinator.data.meters
        )
