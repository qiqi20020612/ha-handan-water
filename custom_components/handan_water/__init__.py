"""Read-only Handan Water account and meter integration."""

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import CoreState, Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import HandanWaterClient
from .const import CONF_ACCOUNT, CONF_ALLOW_HTTP, CONF_TRANSPORT
from .coordinator import HandanWaterCoordinator
from .entity import account_device_info
from .history import History
from .models import InvalidData, account_identity

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]
type HandanWaterConfigEntry = ConfigEntry[HandanWaterCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: HandanWaterConfigEntry) -> bool:
    transport = entry.data.get(CONF_TRANSPORT, "https")
    if transport == "http" and not entry.data.get(CONF_ALLOW_HTTP, False):
        raise ConfigEntryError("HTTP 明文传输尚未获得同意，请重新配置")
    history = History(hass, entry.entry_id, account_identity(entry.data[CONF_ACCOUNT]))
    try:
        await history.async_load()
    except InvalidData as err:
        raise ConfigEntryError(str(err)) from None
    session = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
    coordinator = HandanWaterCoordinator(
        hass, entry, HandanWaterClient(session, entry.data[CONF_ACCOUNT], transport), history
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    # Platform entity addition is scheduled asynchronously. Register the parent
    # first so every meter can resolve its scoped via_device_id immediately.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **account_device_info(history.account_key)
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    if hass.state is not CoreState.running:

        async def on_start(_event: Event) -> None:
            await coordinator.async_sync_history()

        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, on_start))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HandanWaterConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: HandanWaterConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: HandanWaterConfigEntry) -> None:
    # Unloading/reloading preserves the ledger. Only actual entry deletion removes it.
    # Recorder statistics remain under the user's control in HA's Statistics UI.
    history = History(hass, entry.entry_id, account_identity(entry.data[CONF_ACCOUNT]))
    await history.store.async_remove()
