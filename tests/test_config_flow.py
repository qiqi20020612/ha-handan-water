"""Exercise HA's flow manager, privacy consent and account identity rules."""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.handan_water.const import (
    CONF_ACCOUNT,
    CONF_ALLOW_HTTP,
    CONF_TRANSPORT,
    DOMAIN,
)
from custom_components.handan_water.models import BusinessError, CannotConnect, account_identity

ACCOUNT = "00001234"
pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_native_form_and_https_success(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    defaults = result["data_schema"]({CONF_ACCOUNT: ACCOUNT})
    assert defaults[CONF_ALLOW_HTTP] is False
    with (
        patch(
            "custom_components.handan_water.config_flow.validate_account", return_value="https"
        ) as validate,
        patch("custom_components.handan_water.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ACCOUNT: ACCOUNT, CONF_ALLOW_HTTP: False}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ACCOUNT] == ACCOUNT
    assert result["data"][CONF_TRANSPORT] == "https"
    assert result["result"].unique_id == account_identity(ACCOUNT)
    assert ACCOUNT not in result["title"]
    validate.assert_awaited_once_with(hass, ACCOUNT, False)


async def test_duplicate_account_does_not_query(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_ACCOUNT: ACCOUNT}, unique_id=account_identity(ACCOUNT)
    )
    entry.add_to_hass(hass)
    with patch("custom_components.handan_water.config_flow.validate_account") as validate:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data={CONF_ACCOUNT: ACCOUNT, CONF_ALLOW_HTTP: False},
        )
    assert result["reason"] == "already_configured"
    validate.assert_not_called()


@pytest.mark.parametrize(
    ("error", "expected"),
    [(CannotConnect("safe"), "cannot_connect"), (BusinessError("safe"), "business_error")],
)
async def test_flow_errors_keep_form(hass, error, expected):
    with patch("custom_components.handan_water.config_flow.validate_account", side_effect=error):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data={CONF_ACCOUNT: ACCOUNT, CONF_ALLOW_HTTP: False},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


@pytest.mark.parametrize(("allow", "transports"), [(False, ["https"]), (True, ["https", "http"])])
async def test_plaintext_only_after_explicit_consent(hass, load_json, allow, transports):
    from custom_components.handan_water.config_flow import validate_account

    seen = []

    async def query(client, service, today):
        assert type(client.session.cookie_jar).__name__ == "DummyCookieJar"
        seen.append(client.transport)
        if client.transport == "https":
            raise CannotConnect("safe")
        body = load_json("response_37.json")["data"]["body"]
        body["consNo"] = ACCOUNT
        return body

    with patch(
        "custom_components.handan_water.api.HandanWaterClient.query",
        autospec=True,
        side_effect=query,
    ):
        if allow:
            assert await validate_account(hass, ACCOUNT, allow) == "http"
        else:
            with pytest.raises(CannotConnect):
                await validate_account(hass, ACCOUNT, allow)
    assert seen == transports


async def test_business_error_does_not_downgrade_transport(hass):
    from custom_components.handan_water.config_flow import validate_account

    with patch(
        "custom_components.handan_water.api.HandanWaterClient.query",
        side_effect=BusinessError("safe"),
    ) as query:
        with pytest.raises(BusinessError):
            await validate_account(hass, ACCOUNT, True)
    assert query.await_count == 1


async def test_reconfigure_identity_and_options(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ACCOUNT: ACCOUNT, CONF_ALLOW_HTTP: False, CONF_TRANSPORT: "https"},
        unique_id=account_identity(ACCOUNT),
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    with (
        patch("custom_components.handan_water.config_flow.validate_account", return_value="http"),
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ACCOUNT: ACCOUNT, CONF_ALLOW_HTTP: True}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_TRANSPORT] == "http"
    options = await hass.config_entries.options.async_init(entry.entry_id)
    assert options["type"] is FlowResultType.FORM
    options = await hass.config_entries.options.async_configure(
        options["flow_id"],
        user_input={
            "import_history": False,
            "query_payments": False,
            "query_analysis": True,
            "history_attribute_limit": 6,
        },
    )
    assert options["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["history_attribute_limit"] == 6
