"""Tests use only synthetic protocol fixtures and isolated HA instances."""

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from custom_components.handan_water.api import parse_envelope
from custom_components.handan_water.models import (
    CHINA_TZ,
    Snapshot,
    parse_account,
    parse_bills,
    parse_payments,
)

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture
def load_json():
    def load(name):
        return json.loads((Path(__file__).parent / "fixtures" / name).read_text())

    return load


@pytest.fixture
def sample(load_json):
    return Snapshot(
        parse_account(parse_envelope(load_json("response_37.json"), "200001"), "DEMO_ACCOUNT_001"),
        parse_bills(parse_envelope(load_json("response_27.json"), "200005"), date(2026, 10, 19)),
        parse_payments(parse_envelope(load_json("response_52.json"), "200008")),
        None,
        datetime(2026, 10, 19, 16, 30, tzinfo=CHINA_TZ),
    )
