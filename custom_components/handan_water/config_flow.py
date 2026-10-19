"""Native UI configuration, transport consent, reconfiguration and options."""

from datetime import datetime
from typing import Any

import aiohttp
import probatio as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import HandanWaterClient
from .const import (
    CONF_ACCOUNT,
    CONF_ALLOW_HTTP,
    CONF_ANALYSIS,
    CONF_HISTORY,
    CONF_HISTORY_LIMIT,
    CONF_PAYMENTS,
    CONF_TRANSPORT,
    DEFAULT_HISTORY_LIMIT,
    DOMAIN,
    NAME,
)
from .models import (
    CHINA_TZ,
    AuthenticationRequired,
    BusinessError,
    CannotConnect,
    InvalidAccount,
    InvalidData,
    account_identity,
    normalize_account,
    parse_account,
)


async def validate_account(hass, account: str, allow_http: bool) -> str:
    """Start with TLS; explicit UI consent is required before a plaintext retry."""
    session = async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar()
    )
    try:
        for transport in ("https", "http") if allow_http else ("https",):
            client = HandanWaterClient(session, account, transport)
            try:
                body = await client.query("200001", datetime.now(CHINA_TZ).date())
            except CannotConnect:
                if transport == "https" and allow_http:
                    continue
                raise
            parse_account(body, account)
            return transport
    finally:
        session.detach()
    raise CannotConnect("无法连接供水查询服务")


def user_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_ACCOUNT, default=defaults.get(CONF_ACCOUNT, "")): str,
            vol.Required(CONF_ALLOW_HTTP, default=defaults.get(CONF_ALLOW_HTTP, False)): bool,
        }
    )


class HandanWaterConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _async_configure(
        self, user_input: dict[str, Any] | None, *, reconfigure: bool = False
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry() if reconfigure else None
        defaults = dict(entry.data) if entry else {}
        if user_input is not None:
            defaults.update(user_input)
            try:
                account = normalize_account(user_input[CONF_ACCOUNT])
                unique_id = account_identity(account)
                await self.async_set_unique_id(unique_id)
                if entry and entry.unique_id != unique_id:
                    # Changing the account would attach old entity/statistics identities
                    # to another household. Add another entry for another account.
                    return self.async_abort(reason="account_mismatch")
                if not entry:
                    self._abort_if_unique_id_configured()
                transport = await validate_account(self.hass, account, user_input[CONF_ALLOW_HTTP])
            except InvalidAccount:
                errors["base"] = "invalid_account"
            except AuthenticationRequired:
                errors["base"] = "authentication_required"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except BusinessError:
                errors["base"] = "business_error"
            except InvalidData:
                errors["base"] = "invalid_data"
            else:
                data = {
                    CONF_ACCOUNT: account,
                    CONF_ALLOW_HTTP: user_input[CONF_ALLOW_HTTP],
                    CONF_TRANSPORT: transport,
                }
                title = f"{NAME} {unique_id[:8]}"
                if entry:
                    return self.async_update_reload_and_abort(entry, data=data, title=title)
                return self.async_create_entry(title=title, data=data)
        return self.async_show_form(
            step_id="reconfigure" if reconfigure else "user",
            data_schema=user_schema(defaults),
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_configure(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_configure(user_input, reconfigure=True)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> HandanWaterOptionsFlow:
        return HandanWaterOptionsFlow()


class HandanWaterOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HISTORY, default=options.get(CONF_HISTORY, True)): bool,
                    vol.Required(CONF_PAYMENTS, default=options.get(CONF_PAYMENTS, True)): bool,
                    vol.Required(CONF_ANALYSIS, default=options.get(CONF_ANALYSIS, False)): bool,
                    vol.Required(
                        CONF_HISTORY_LIMIT,
                        default=options.get(CONF_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
                }
            ),
        )
