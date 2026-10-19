"""Persistence, real Recorder upserts, recovery and dangerous source changes."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    async_list_statistic_ids,
)
from homeassistant.core import CoreState
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_recorder_block_till_done,
)

from custom_components.handan_water.history import History
from custom_components.handan_water.models import InvalidData, account_identity


async def test_ledger_rollover_restart_and_revisions(hass, sample):
    history = History(hass, "test_entry", account_identity("00001234"))
    await history.async_merge(sample)
    await history.async_merge(sample)
    assert len(history.bills) == 12
    assert len(history.payments) == 12
    restored = History(hass, "test_entry", history.account_key)
    await restored.async_load()
    assert restored.bills == history.bills
    assert restored.payments == history.payments
    # Rolling window omitting old rows does not erase the persistent baseline.
    await restored.async_merge(replace(sample, bills=sample.bills[:2], payments=()))
    assert len(restored.bills) == 12
    changed = replace(
        sample.bills[0],
        end_reading=sample.bills[0].end_reading - 1,
        volume=sample.bills[0].volume - 1,
    )
    await restored.async_merge(replace(sample, bills=(changed,)))
    assert restored.water_issue(changed.meter)
    assert restored.water_issue("DEMO_METER_002") is None
    again = History(hass, "test_entry", history.account_key)
    await again.async_load()
    assert again.source_blocks == restored.source_blocks


async def test_mismatched_or_corrupt_store_is_not_overwritten(hass, sample, hass_storage):
    history = History(hass, "test_entry", account_identity("00001234"))
    await history.async_merge(sample)
    wrong = History(hass, "test_entry", account_identity("00005678"))
    with pytest.raises(InvalidData):
        await wrong.async_load()
    restored = History(hass, "test_entry", history.account_key)
    await restored.async_load()
    assert len(restored.bills) == 12


async def test_real_recorder_roundtrip_and_restart_are_idempotent(recorder_mock, hass, sample):
    await hass.async_start()
    history = History(hass, "test_entry", account_identity("00001234"))
    await history.async_merge(sample)
    with patch(
        "custom_components.handan_water.history.async_add_external_statistics",
        wraps=async_add_external_statistics,
    ) as imported:
        await history.async_sync(True)
        assert history.status == "同步完成", history.issues
        assert imported.call_count == 4
        await history.async_sync(True)
        assert imported.call_count == 4
        restarted = History(hass, "test_entry", history.account_key)
        await restarted.async_load()
        await restarted.async_sync(True)
        assert restarted.status == "同步完成", restarted.issues
        assert imported.call_count == 4
    assert len(restarted.committed) == 4
    for meter, volume, cost in (("DEMO_METER_001", 27, "135"), ("DEMO_METER_002", 9, "45")):
        water_id = restarted.statistic_id(meter, "water")
        water = await restarted._async_read(water_id)
        assert len(water) == 7
        assert Decimal(water[max(water)][1]) == volume
        costs = await restarted._async_read(restarted.statistic_id(meter, "cost"))
        assert Decimal(costs[max(costs)][1]) == Decimal(cost)
    metadata = await async_list_statistic_ids(hass, statistic_ids=set(restarted.committed))
    assert len(metadata) == 4
    assert {item["unit_class"] for item in metadata} == {None, "volume"}
    assert all(item["mean_type"] == 0 and item["has_sum"] for item in metadata)


async def test_interrupted_checkpoint_and_missing_db_rows_recover(recorder_mock, hass, sample):
    await hass.async_start()
    history = History(hass, "test_entry", account_identity("00001234"))
    await history.async_merge(sample)
    with patch.object(history, "async_save", side_effect=OSError("simulated process interruption")):
        with pytest.raises(OSError):
            await history.async_sync(True)
    recovered = History(hass, "test_entry", history.account_key)
    await recovered.async_load()
    await recovered.async_sync(True)
    assert recovered.status == "同步完成", recovered.issues
    assert all(len(rows) == 7 for rows in recovered.committed.values())
    ids = list(recovered.committed)
    recorder_mock.async_clear_statistics(ids)
    await async_recorder_block_till_done(hass)
    assert await recovered._async_read(ids[0]) == {}
    await recovered.async_sync(True)
    assert recovered.status == "同步完成", recovered.issues
    assert len(await recovered._async_read(ids[0])) == 7


async def test_reissued_bill_or_historical_cost_correction_does_not_rewrite(
    recorder_mock, hass, sample
):
    await hass.async_start()
    history = History(hass, "test_entry", account_identity("00001234"))
    await history.async_merge(sample)
    await history.async_sync(True)
    meter = sample.bills[0].meter
    stat_id = history.statistic_id(meter, "cost")
    before = await history._async_read(stat_id)
    # Different id, same interval: water remains coalesced; cost is quarantined.
    duplicate = replace(sample.bills[0], key="reissued_bill")
    await history.async_merge(replace(sample, bills=(duplicate,)))
    await history.async_sync(True)
    assert stat_id in history.issues
    assert await history._async_read(stat_id) == before
    assert history.statistic_id("DEMO_METER_002", "cost") not in history.issues


async def test_history_disabled_and_no_recorder_are_explicit(hass, sample):
    history = History(hass, "test_entry", account_identity("00001234"))
    await history.async_merge(sample)
    await history.async_sync(False)
    assert history.status == "历史导入已关闭"
    assert not history.committed
    await history.async_sync(True)
    assert history.status == "等待 Recorder 启用"


async def test_starting_state_defers_recorder_wait(hass, sample):
    history = History(hass, "test_entry", account_identity("00001234"))
    await history.async_merge(sample)
    hass.config.components.add("recorder")
    hass.set_state(CoreState.starting)
    assert hass.is_running  # This includes 'starting'; it cannot be the guard.
    await history.async_sync(True)
    assert history.status == "等待 Home Assistant 启动"
    hass.config.components.remove("recorder")
    hass.set_state(CoreState.running)


async def test_new_interval_same_bill_month_appends_without_reset(recorder_mock, hass, sample):
    history = History(hass, "test_entry", account_identity("00001234"))
    await history.async_merge(sample)
    await history.async_sync(True)
    latest = sample.bills[0]
    later = replace(
        latest,
        key="new_bill_same_accounting_month",
        start=latest.end,
        end=date(2026, 9, 25),
        start_reading=latest.end_reading,
        end_reading=Decimal(1033),
        volume=Decimal(6),
        amount=Decimal(30),
        fees=(),
    )
    await history.async_merge(replace(sample, bills=(later,), payments=None))
    await history.async_sync(True)
    assert history.status == "同步完成", history.issues
    rows = await history._async_read(history.statistic_id(latest.meter, "water"))
    assert len(rows) == 8
    assert Decimal(rows[max(rows)][1]) == 33
    assert history.water_issue(latest.meter) is None
    cost_rows = await history._async_read(history.statistic_id(latest.meter, "cost"))
    assert Decimal(cost_rows[max(cost_rows)][1]) == Decimal("165")


async def test_amount_revision_and_missing_store_preserve_existing_statistics(
    recorder_mock, hass, sample
):
    history = History(hass, "test_entry", account_identity("00001234"))
    await history.async_merge(sample)
    await history.async_sync(True)
    meter = sample.bills[0].meter
    cost_id = history.statistic_id(meter, "cost")
    before = await history._async_read(cost_id)
    changed = replace(sample.bills[0], amount=Decimal(40), fees=())
    await history.async_merge(replace(sample, bills=(changed,), payments=None))
    await history.async_sync(True)
    assert cost_id in history.issues
    assert await history._async_read(cost_id) == before
    assert history.statistic_id(meter, "water") not in history.issues
    # Losing Store cannot rebase an existing Recorder series to a shorter window.
    lost = History(hass, "new_empty_store", history.account_key)
    await lost.async_merge(replace(sample, bills=sample.bills[:2], payments=None))
    await lost.async_sync(True)
    assert lost.issues
    assert await lost._async_read(cost_id) == before
