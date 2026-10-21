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
from custom_components.handan_water.models import (
    CannotConnect,
    InvalidData,
    account_identity,
    meter_identity,
)

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


@pytest.mark.parametrize("existing_devices", [False, True], ids=["new-install", "existing-install"])
async def test_device_links_use_registry_ids_without_deprecations(
    hass, query_mock, caplog, existing_devices
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ACCOUNT: "00001234", CONF_ALLOW_HTTP: True, CONF_TRANSPORT: "http"},
        unique_id=account_identity("00001234"),
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    account_key = account_identity("00001234")
    meters = ("DEMO_METER_001", "DEMO_METER_002")
    before = {}
    if existing_devices:
        # The old via_device parameter was already stored as via_device_id by
        # HA. Seed that registry state without calling a deprecated API.
        parent = registry.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, account_key)}, name="合成账户"
        )
        before[account_key] = parent.id
        registry.async_update_device(parent.id, name_by_user="自定义账户名称")
        for meter in meters:
            key = meter_identity(account_key, meter)
            device = registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, key)},
                via_device_id=parent.id,
                name=f"合成水表 {meter}",
            )
            before[key] = device.id

    with patch.object(
        registry, "async_get_or_create", wraps=registry.async_get_or_create
    ) as create:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert create.call_count > 0
        assert all("via_device" not in call.kwargs for call in create.call_args_list)

    parent = registry.async_get_device_by_identifier((DOMAIN, account_key), entry.entry_id)
    assert parent is not None
    assert parent.via_device_id is None
    if existing_devices:
        assert parent.id == before[account_key]
        assert parent.name_by_user == "自定义账户名称"
    devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(devices) == 3
    for meter in meters:
        key = meter_identity(account_key, meter)
        device = registry.async_get_device_by_identifier((DOMAIN, key), entry.entry_id)
        assert device is not None
        assert device.via_device_id == parent.id
        if existing_devices:
            assert device.id == before[key]
        entity_id = get_entity(hass, "00001234", "reading", meter)
        assert er.async_get(hass).async_get(entity_id).device_id == device.id
        assert hass.states.get(entity_id).state != "unavailable"
    assert not any(
        record.name == "homeassistant.helpers.frame" and "deprecated" in record.getMessage()
        for record in caplog.records
    )


async def test_two_accounts_and_reload_keep_independent_identities(hass, query_mock):
    entry = await setup(hass)
    another = await setup(hass, "00005678")
    first = get_entity(hass, "00001234", "reading", "DEMO_METER_001")
    second = get_entity(hass, "00005678", "reading", "DEMO_METER_001")
    registry = dr.async_get(hass)
    before = {
        device.id: device.via_device_id
        for account_entry in (entry, another)
        for device in dr.async_entries_for_config_entry(registry, account_entry.entry_id)
    }
    for account_entry in (entry, another):
        devices = dr.async_entries_for_config_entry(registry, account_entry.entry_id)
        parent = next(device for device in devices if device.via_device_id is None)
        assert all(
            device.id == parent.id or device.via_device_id == parent.id for device in devices
        )
    assert first != second
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert get_entity(hass, "00001234", "reading", "DEMO_METER_001") == first
    assert len(entry.runtime_data.history.bills) == 12
    assert len(another.runtime_data.history.bills) == 12
    assert {
        device.id: device.via_device_id
        for account_entry in (entry, another)
        for device in dr.async_entries_for_config_entry(registry, account_entry.entry_id)
    } == before
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


@pytest.mark.parametrize(
    "failure",
    [OSError("recorder unavailable"), TimeoutError(), InvalidData("unconfirmed write")],
    ids=["recorder-error", "recorder-timeout", "unconfirmed-write"],
)
async def test_recorder_failure_does_not_hide_valid_readings(hass, query_mock, failure):
    # Exercise the real coordinator and HA entity states; only the Recorder
    # boundary is replaced. Actual Recorder retries are covered in test_history.
    hass.config.components.add("recorder")
    hass.set_state(CoreState.running)
    with patch(
        "custom_components.handan_water.history.History._async_import", side_effect=failure
    ) as imported:
        entry = await setup(hass)
        coordinator = entry.runtime_data
        readings = [
            (get_entity(hass, "00001234", "reading", meter), expected)
            for meter, expected in (("DEMO_METER_001", "1027"), ("DEMO_METER_002", "209"))
        ]
        status = get_entity(hass, "00001234", "sync_status")
        assert coordinator.last_update_success
        assert all(hass.states.get(entity_id).state == value for entity_id, value in readings)
        assert hass.states.get(status).state == "同步失败，可重试"
        assert len(hass.states.get(status).attributes["统计问题"]) == 4
        assert not coordinator.history.committed

        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert all(hass.states.get(entity_id).state == value for entity_id, value in readings)

        original = query_mock.side_effect
        query_mock.side_effect = CannotConnect("供水服务连接失败或超时")
        await coordinator.async_refresh()
        assert all(hass.states.get(entity_id).state == "unavailable" for entity_id, _ in readings)
        query_mock.side_effect = original
        await coordinator.async_refresh()
        assert all(hass.states.get(entity_id).state == value for entity_id, value in readings)

        imported.side_effect = None
        recover = get_entity(hass, "00001234", "sync_history", platform="button")
        await hass.services.async_call("button", "press", {"entity_id": recover}, blocking=True)
        assert hass.states.get(status).state == "同步完成"
        assert not coordinator.history.issues
        assert all(hass.states.get(entity_id).state == value for entity_id, value in readings)
    hass.config.components.remove("recorder")


@pytest.mark.parametrize("kind", ["revision", "same-day", "regression", "volume-mismatch"])
async def test_reading_conflicts_remain_unavailable_without_recorder(hass, query_mock, kind):
    entry = await setup(hass)
    original = query_mock.side_effect

    async def query(client, service, today):
        body = await original(client, service, today)
        if service == "200005":
            row = dict(body["reclist"][0])
            if kind in {"revision", "same-day"}:
                row["rlecode"] = str(int(row["rlecode"]) + 1)
                row["rlsl"] = str(int(row["rlsl"]) + 1)
            elif kind == "regression":
                row.update(
                    rlprdate="2026/09/15", rlrdate="2026/09/25", rlscode="2", rlecode="4", rlsl="2"
                )
            else:
                row["rlsl"] = str(int(row["rlsl"]) + 1)
            if kind == "revision":
                body["reclist"][0] = row
            else:
                row["rlid"] = "DEMO_CONFLICTING_RECORD"
                body["reclist"].append(row)
        return body

    query_mock.side_effect = query
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.last_update_success
    affected = get_entity(hass, "00001234", "reading", "DEMO_METER_001")
    other = get_entity(hass, "00001234", "reading", "DEMO_METER_002")
    assert hass.states.get(affected).state == "unavailable"
    assert hass.states.get(other).state == "209"
    assert entry.runtime_data.history.water_issue("DEMO_METER_001")
    assert not entry.runtime_data.history.committed
    # A reload or disabling imports must not bypass the same source checks.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.history.async_sync(False)
    entry.runtime_data.async_update_listeners()
    assert hass.states.get(affected).state == "unavailable"
    assert hass.states.get(other).state == "209"


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
    registry = dr.async_get(hass)
    parent = registry.async_get_device_by_identifier(
        (DOMAIN, entry.runtime_data.history.account_key), entry.entry_id
    )
    device = registry.async_get(er.async_get(hass).async_get(new).device_id)
    assert device.via_device_id == parent.id

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
