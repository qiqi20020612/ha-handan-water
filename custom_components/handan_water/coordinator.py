"""Daily polling and independent availability for optional source queries."""

import logging
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HandanWaterClient
from .const import CONF_ANALYSIS, CONF_HISTORY, CONF_PAYMENTS, DOMAIN, UPDATE_INTERVAL
from .history import History
from .models import (
    CHINA_TZ,
    Snapshot,
    WaterError,
    parse_account,
    parse_analysis,
    parse_bills,
    parse_payments,
)

_LOGGER = logging.getLogger(__name__)


class HandanWaterCoordinator(DataUpdateCoordinator[Snapshot]):
    def __init__(
        self, hass: HomeAssistant, entry, client: HandanWaterClient, history: History
    ) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=UPDATE_INTERVAL, config_entry=entry
        )
        self.client = client
        self.history = history
        self.options = entry.options

    async def _async_update_data(self) -> Snapshot:
        now = datetime.now(CHINA_TZ)
        try:
            account = parse_account(
                await self.client.query("200001", now.date()), self.client.account
            )
            bills = parse_bills(await self.client.query("200005", now.date()), now.date())
            payments = analysis = None
            payment_error = analysis_error = None
            if self.options.get(CONF_PAYMENTS, True):
                try:
                    payments = parse_payments(await self.client.query("200008", now.date()))
                except WaterError:
                    payment_error = "缴费记录查询失败"
            if self.options.get(CONF_ANALYSIS, False):
                try:
                    analysis = parse_analysis(await self.client.query("200020", now.date()))
                except WaterError:
                    analysis_error = "网站用水分析查询失败"
            snapshot = Snapshot(
                account, bills, payments, analysis, now, payment_error, analysis_error
            )
            await self.history.async_merge(snapshot)
            await self.history.async_sync(self.options.get(CONF_HISTORY, True))
            return snapshot
        except WaterError as err:
            raise UpdateFailed(str(err)) from None
        except Exception:
            raise UpdateFailed("数据刷新或本地历史保存失败") from None

    async def async_sync_history(self) -> None:
        await self.history.async_sync(self.options.get(CONF_HISTORY, True))
        self.async_update_listeners()
