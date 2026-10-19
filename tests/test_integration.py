"""Real HA platform setup, multiple accounts, updates and lifecycle."""

import json
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.handan_water.const import (
    CONF_ACCOUNT,
    CONF_ALLOW_HTTP,
    CONF_TRANSPORT,
    DOMAIN,
)
from custom_components.handan_water.models import CannotConnect, account_identity, meter_identity

pytestmark = [
    pytest.mark.freeze_time("2026-10-19T08:30:00Z", real_asyncio=True),
    pytest.mark.usefixtures("enable_custom_integrations"),
]


@pytest.fixture
def query_mock(load_json):
    async def query(client, service, today):
        num = {"200001": 37, "200005": 27, "200008": 52, "200020": 58}[service]
        body = load_json(f"response_{num}.json")["data"]["body"]
        if service == "200001":
            body["consNo"] = client.account
        return body

    with patch(
        "custom_components.handan_water.api.HandanWaterClient.query",
        autospec=True,
        side_effect=query,
    ) as mocked:
        yield mocked


async def setup(hass, account="00001234", options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ACCOUNT: account, CONF_ALLOW_HTTP: True, CONF_TRANSPORT: "http"},
        unique_id=account_identity(account),
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def get_entity(hass, account, key, meter=None, platform="sensor"):
    account_key = account_identity(account)
    device_key = meter_identity(account_key, meter) if meter else account_key
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{device_key}_{key}")


async def test_entities_native_water_and_actual_money(hass, query_mock):
    entry = await setup(hass)
    assert query_mock.await_count == 3
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 3
    assert len([device for device in devices if device.via_device_id]) == 2
    for meter, expected in (("DEMO_METER_001", "1027"), ("DEMO_METER_002", "209")):
        state = hass.states.get(get_entity(hass, "00001234", "reading", meter))
        assert state.state == expected
        assert state.attributes["unit_of_measurement"] == "m³"
        assert state.attributes["device_class"] == "water"
        assert state.attributes["state_class"] == "total_increasing"
        assert state.attributes["水表编号"] == meter
        assert "handan_water:" in state.attributes["历史水量统计ID"]
    assert hass.states.get(get_entity(hass, "00001234", "recent_payment")).state == "12"
    assert (
        hass.states.get(get_entity(hass, "00001234", "period_cost", "DEMO_METER_001")).state == "10"
    )
    registry = er.async_get(hass)
    assert (
        registry.async_get(get_entity(hass, "00001234", "analysis")).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )


async def test_two_accounts_and_reload_keep_independent_identities(hass, query_mock):
    entry = await setup(hass)
    another = await setup(hass, "00005678")
    first = get_entity(hass, "00001234", "reading", "DEMO_METER_001")
    second = get_entity(hass, "00005678", "reading", "DEMO_METER_001")
    assert first != second
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert get_entity(hass, "00001234", "reading", "DEMO_METER_001") == first
    assert len(entry.runtime_data.history.bills) == 12
    assert len(another.runtime_data.history.bills) == 12
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hass.states.get(second).state == "1027"


async def test_failure_refresh_recovery_and_daily_poll(hass, query_mock, freezer):
    entry = await setup(hass)
    refresh = get_entity(hass, "00001234", "refresh", platform="button")
    reading = get_entity(hass, "00001234", "reading", "DEMO_METER_001")
    original = query_mock.side_effect
    query_mock.side_effect = CannotConnect("供水服务连接失败或超时")
    await hass.services.async_call("button", "press", {"entity_id": refresh}, blocking=True)
    assert hass.states.get(reading).state == "unavailable"
    assert hass.states.get(refresh).state != "unavailable"
    query_mock.side_effect = original
    await hass.services.async_call("button", "press", {"entity_id": refresh}, blocking=True)
    assert hass.states.get(reading).state == "1027"
    before = query_mock.await_count
    freezer.tick(timedelta(days=1, seconds=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert query_mock.await_count == before + 3
    assert len(entry.runtime_data.history.bills) == 12


async def test_optional_failure_does_not_erase_meter_data(hass, query_mock):
    original = query_mock.side_effect

    async def query(client, service, today):
        if service == "200008":
            raise CannotConnect("safe")
        return await original(client, service, today)

    query_mock.side_effect = query
    entry = await setup(hass)
    assert (
        hass.states.get(get_entity(hass, "00001234", "reading", "DEMO_METER_001")).state == "1027"
    )
    assert entry.runtime_data.data.payments is None
    assert entry.runtime_data.data.payment_error
    assert hass.states.get(get_entity(hass, "00001234", "recent_payment")).state == "12"


async def test_startup_listener_and_recovery_button(hass, query_mock):
    hass.set_state(CoreState.starting)
    entry = await setup(hass)
    with patch.object(entry.runtime_data.history, "async_sync") as sync:
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
        sync.assert_awaited_once_with(True)
        button = get_entity(hass, "00001234", "sync_history", platform="button")
        await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
        assert sync.await_count == 2


async def test_empty_bills_and_dynamic_new_meter(hass, query_mock):
    entry = await setup(hass)
    original = query_mock.side_effect

    async def new_meter(client, service, today):
        body = await original(client, service, today)
        if service == "200005":
            row = {**body["reclist"][0], "rlmcode": "DEMO_METER_NEW", "rlid": "DEMO_RECORD_NEW"}
            body["reclist"].append(row)
        return body

    query_mock.side_effect = new_meter
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    new = get_entity(hass, "00001234", "reading", "DEMO_METER_NEW")
    assert hass.states.get(new).state == "1027"

    async def empty_bills(client, service, today):
        body = await original(client, service, today)
        if service == "200005":
            body["reclist"] = []
        return body

    query_mock.side_effect = empty_bills
    await entry.runtime_data.async_refresh()
    assert hass.states.get(new).state == "unavailable"
    assert len(entry.runtime_data.history.bills) == 13


async def test_delete_entry_removes_only_its_ledger(hass, query_mock, hass_storage):
    entry = await setup(hass)
    another = await setup(hass, "00005678")
    assert f"handan_water.{entry.entry_id}" in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    assert f"handan_water.{entry.entry_id}" not in hass_storage
    assert f"handan_water.{another.entry_id}" in hass_storage


async def test_history_attributes_stay_within_recorder_limit(hass, query_mock):
    entry = await setup(hass, options={"history_attribute_limit": 120})
    history = entry.runtime_data.history
    bill = entry.runtime_data.data.bills[0]
    for index in range(120):
        duplicate = replace(bill, key=f"synthetic_{index}")
        history.bills[duplicate.key] = duplicate
    entry.runtime_data.async_update_listeners()
    state = hass.states.get(get_entity(hass, "00001234", "bill_history", bill.meter))
    assert int(state.state) == 126
    assert 0 < len(state.attributes["账单记录"]) < 120
    assert len(json.dumps(dict(state.attributes), ensure_ascii=False).encode()) < 16384
