"""Local refresh/recovery controls, never supply-side write operations."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import HandanWaterEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            HandanWaterButton(coordinator, "refresh", "刷新数据"),
            HandanWaterButton(coordinator, "sync_history", "恢复历史同步"),
        ]
    )


class HandanWaterButton(HandanWaterEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        # Refresh must remain usable after a failed coordinator refresh.
        return True

    async def async_press(self) -> None:
        if self.key == "refresh":
            await self.coordinator.async_refresh()
        else:
            await self.coordinator.async_sync_history()
