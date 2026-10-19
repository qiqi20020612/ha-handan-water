"""Read-only HTTP client using only requests verified in the capture/JS."""

import asyncio
import json
from datetime import date
from typing import Any

import aiohttp

from .const import API_HOST, API_ID, API_PATH, MAX_RESPONSE_BYTES, READ_SERVICES, REQUEST_TIMEOUT
from .models import (
    AuthenticationRequired,
    BusinessError,
    CannotConnect,
    InvalidData,
    js_months_ago,
    obj,
)


def build_request(service: str, account: str, today: date) -> dict[str, Any]:
    if service not in READ_SERVICES:
        raise ValueError("仅允许已审查的只读查询")
    body = {"queryType": "0", "queryValue": account}
    earlier = js_months_ago(today)
    if service == "200005":
        body.update(endYm=today.strftime("%Y.%m"), bgnYm=earlier.strftime("%Y.%m"))
    elif service == "200008":
        body.update(bgnDate=today.strftime("%Y%m%d"), endDate=earlier.strftime("%Y%m%d"))
    return {
        "id": API_ID,
        "param": {"in_json": {"head": {"servCode": service, "version": "1.0.1"}, "body": body}},
    }


def parse_envelope(payload: Any, service: str) -> dict[str, Any]:
    envelope = obj(payload)
    if envelope.get("result") != "ok":
        raise BusinessError("供水服务返回业务错误，请核对户号或稍后重试")
    data = obj(envelope.get("data"))
    if obj(data.get("head")).get("servCode") != service:
        raise InvalidData("返回服务码不匹配")
    body = obj(data.get("body"))
    if "rtnCode" not in body and service != "200005":
        raise InvalidData("业务返回码缺失")
    if "rtnCode" in body and str(body["rtnCode"]) != "9999":
        raise BusinessError("供水服务返回业务错误，请核对户号或稍后重试")
    return body


class HandanWaterClient:
    def __init__(
        self, session: aiohttp.ClientSession, account: str, transport: str = "https"
    ) -> None:
        if transport not in {"https", "http"}:
            raise ValueError("无效传输方式")
        self.session = session
        self.account = account
        self.transport = transport

    async def query(self, service: str, today: date) -> dict[str, Any]:
        request = build_request(service, self.account, today)
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self.session.post(
                    f"{self.transport}://{API_HOST}{API_PATH}",
                    json=request,
                    headers={"Accept": "application/json"},
                    allow_redirects=False,
                ) as response:
                    if response.status in (401, 403):
                        raise AuthenticationRequired("服务要求额外认证")
                    if response.status != 200:
                        raise CannotConnect("供水服务 HTTP 请求失败")
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise InvalidData("响应超过大小限制")
                    try:
                        payload = json.loads(raw)
                    except ValueError, UnicodeError, RecursionError:
                        raise InvalidData("响应不是有效 JSON") from None
        except aiohttp.ClientError, TimeoutError, OSError:
            # Server messages, URLs, request arguments and payloads are not logged.
            raise CannotConnect("供水服务连接失败或超时") from None
        return parse_envelope(payload, service)
