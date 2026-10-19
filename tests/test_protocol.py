"""Verified protocol, monetary semantics and deliberately hostile responses."""

import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import aiohttp
import pytest

from custom_components.handan_water.api import HandanWaterClient, build_request, parse_envelope
from custom_components.handan_water.const import MAX_RESPONSE_BYTES
from custom_components.handan_water.models import (
    AuthenticationRequired,
    BusinessError,
    CannotConnect,
    InvalidAccount,
    InvalidData,
    js_months_ago,
    normalize_account,
    parse_analysis,
    parse_bills,
    parse_payments,
)

URL = "http://www.hdwsc.com.cn/adsWeChat/pt.action"


class Response:
    """Small public-interface fake, independent of aiohttp private constructors."""

    def __init__(self, status=200, body=b"{}"):
        self.status = status
        self.body = body.encode() if isinstance(body, str) else body
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i : i + size]


@pytest.mark.parametrize(
    ("service", "number"), [("200001", 37), ("200005", 27), ("200008", 52), ("200020", 58)]
)
def test_verified_request_shape(load_json, service, number):
    assert build_request(service, "DEMO_ACCOUNT_001", date(2026, 10, 19)) == load_json(
        f"request_{number}.json"
    )


def test_only_reviewed_operations():
    for service in ("pay", "200002", "addSysLog", "changeBindInfo", "JSASD7"):
        with pytest.raises(ValueError):
            build_request(service, "00000001", date(2026, 10, 19))


def test_js_date_rollover_and_leading_zero_account():
    assert js_months_ago(date(2028, 2, 29)) == date(2027, 3, 1)
    assert js_months_ago(date(2026, 1, 31), 2) == date(2025, 12, 1)
    assert normalize_account(" 00000123 ") == "00000123"
    for value in ("", 123, "DEMO_ACCOUNT_001", "१२३४", "001&openid=x"):
        with pytest.raises(InvalidAccount):
            normalize_account(value)


def test_sample_bill_and_payment_semantics(sample):
    assert len(sample.bills) == 12
    assert len({bill.meter for bill in sample.bills}) == 2
    newest = sorted(sample.bills, key=lambda bill: bill.end, reverse=True)[:2]
    assert {bill.end_reading for bill in newest} == {Decimal(1027), Decimal(209)}
    assert {bill.volume for bill in newest} == {Decimal(2), Decimal(1)}
    assert sum(bill.amount for bill in newest) == Decimal("15")
    payment = sample.payments[-1]
    assert payment.paid == Decimal("12")
    assert payment.settled == Decimal("15")
    assert payment.deduction == Decimal("3")
    assert any(
        payment.paid > 0 and payment.settled == 0 and payment.prepaid_change > 0
        for payment in sample.payments
    )


def test_analysis_is_not_account_total(load_json, sample):
    trend = parse_analysis(parse_envelope(load_json("response_58.json"), "200020"))
    assert trend[0] == ("2026.09", Decimal(2))
    assert sum(bill.volume for bill in sample.bills if bill.month == "2026.09") == 3


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"result": "ok", "data": []},
        {"result": "ok", "data": {"head": {"servCode": "wrong"}, "body": {}}},
    ],
)
def test_invalid_envelopes(payload):
    with pytest.raises((InvalidData, BusinessError)):
        parse_envelope(payload, "200001")


def test_business_codes_and_missing_bill_code(load_json):
    payload = load_json("response_37.json")
    assert parse_envelope(payload, "200001")["rtnCode"] == "9999"
    payload["data"]["body"]["rtnCode"] = "UNOBSERVED_FAILURE"
    payload["data"]["body"]["rtnMsg"] = "PRIVATE_SERVER_ERROR"
    with pytest.raises(BusinessError, match="业务错误") as err:
        parse_envelope(payload, "200001")
    assert "PRIVATE" not in str(err.value)
    assert "rtnCode" not in parse_envelope(load_json("response_27.json"), "200005")
    payload["data"]["body"].pop("rtnCode")
    with pytest.raises(InvalidData):
        parse_envelope(payload, "200001")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rlmcode", ""),
        ("rlscode", "NaN"),
        ("rlecode", "Infinity"),
        ("rlsl", -1),
        ("rlmonth", "2026.99"),
        ("rlrdate", "2026/02/30"),
        ("recdetail", None),
    ],
)
def test_bill_schema_changes_are_not_zero(load_json, field, value):
    body = load_json("response_27.json")["data"]["body"]
    body["reclist"][0][field] = value
    with pytest.raises(InvalidData):
        parse_bills(body, date(2026, 10, 19))


def test_empty_data_and_duplicate_rows(load_json):
    assert parse_bills({"reclist": []}, date(2026, 10, 19)) == ()
    assert parse_payments({}) == ()
    with pytest.raises(InvalidData):
        parse_bills({}, date(2026, 10, 19))
    body = load_json("response_27.json")["data"]["body"]
    body["reclist"].append(body["reclist"][0])
    assert len(parse_bills(body, date(2026, 10, 19))) == 12
    body["reclist"][-1] = {**body["reclist"][0], "rlje": 99}
    with pytest.raises(InvalidData):
        parse_bills(body, date(2026, 10, 19))


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, AuthenticationRequired),
        (403, AuthenticationRequired),
        (500, CannotConnect),
        (302, CannotConnect),
    ],
)
async def test_http_errors_no_redirect_or_private_logging(status, error, caplog):
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        with patch.object(session, "post", return_value=Response(status, "PRIVATE_BODY")) as mocked:
            with pytest.raises(error):
                await HandanWaterClient(session, "00000123", "http").query(
                    "200001", date(2026, 10, 19)
                )
            assert mocked.call_count == 1
    assert "PRIVATE_BODY" not in caplog.text
    assert "00000123" not in caplog.text


async def test_json_post_without_wechat_headers(load_json):
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        with patch.object(
            session, "post", return_value=Response(body=json.dumps(load_json("response_37.json")))
        ) as mocked:
            await HandanWaterClient(session, "DEMO_ACCOUNT_001", "http").query(
                "200001", date(2026, 10, 19)
            )
            call = mocked.call_args
            assert call.kwargs["json"] == load_json("request_37.json")
            assert call.kwargs["allow_redirects"] is False
            assert not {"cookie", "authorization", "referer", "origin"} & {
                key.lower() for key in call.kwargs["headers"]
            }
            assert "openid" not in json.dumps(call.kwargs["json"])


@pytest.mark.parametrize("failure", [TimeoutError(), aiohttp.ClientConnectionError()])
async def test_network_timeout_is_sanitized(failure):
    async with aiohttp.ClientSession() as session:
        with patch.object(session, "post", side_effect=failure):
            with pytest.raises(CannotConnect):
                await HandanWaterClient(session, "00000123", "http").query(
                    "200001", date(2026, 10, 19)
                )


@pytest.mark.parametrize(
    "body",
    ["<html>PRIVATE</html>", "[]", "x" * (MAX_RESPONSE_BYTES + 1)],
    ids=["html", "array", "too_large"],
)
async def test_invalid_or_oversized_response(body):
    async with aiohttp.ClientSession() as session:
        with patch.object(session, "post", return_value=Response(body=body)):
            with pytest.raises(InvalidData):
                await HandanWaterClient(session, "00000123", "http").query(
                    "200001", date(2026, 10, 19)
                )
