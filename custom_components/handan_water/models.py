"""Strict, privacy-minimizing parsing of the observed protocol."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

CHINA_TZ = ZoneInfo("Asia/Shanghai")
ZERO = Decimal(0)


class WaterError(Exception):
    """An error whose message is safe to expose to HA logs."""


class CannotConnect(WaterError):
    """Network, HTTP or timeout failure."""


class AuthenticationRequired(WaterError):
    """The endpoint rejected an unauthenticated HTTP request."""


class BusinessError(WaterError):
    """Unrecognized business failure; never include the server message."""


class InvalidAccount(WaterError):
    """A success response did not identify the requested account."""


class InvalidData(WaterError):
    """The source no longer conforms to the verified response schema."""


def digest(value: str) -> str:
    """Hash identities without publishing a raw account/meter/transaction."""
    return sha256(value.encode()).hexdigest()


def account_identity(account: str) -> str:
    return digest(f"account:{account}")


def meter_identity(account_key: str, meter: str) -> str:
    return digest(f"{account_key}:meter:{meter}")


def normalize_account(value: Any) -> str:
    """Keep leading zeros; do not invent a utility-specific number length."""
    if not isinstance(value, str):
        raise InvalidAccount("户号格式无效")
    account = value.strip()
    if not account or len(account) > 64 or not account.isascii() or not account.isdigit():
        raise InvalidAccount("户号格式无效")
    return account


def obj(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidData("数据结构发生变化")
    return value


def text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise InvalidData("必要标识缺失")
    return value.strip()


def number(value: Any, *, nonnegative: bool = False) -> Decimal:
    if (
        isinstance(value, bool)
        or value is None
        or not isinstance(value, (str, int, float, Decimal))
    ):
        raise InvalidData("数值字段无效")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise InvalidData("数值字段无效") from None
    if not result.is_finite() or abs(result) > Decimal("1e15") or (nonnegative and result < 0):
        raise InvalidData("数值字段无效")
    return result


def optional_number(value: Any) -> Decimal | None:
    return None if value in (None, "") else number(value)


def reading_date(value: Any) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}/\d{2}/\d{2}", value):
        raise InvalidData("抄表日期格式变化")
    try:
        return datetime.strptime(value, "%Y/%m/%d").date()
    except ValueError:
        raise InvalidData("抄表日期无效") from None


def bill_month(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}\.(0[1-9]|1[0-2])", value):
        raise InvalidData("账期格式变化")
    return value


def js_months_ago(today: date, months: int = 12) -> date:
    """Match util1.js Date.setMonth, including leap-day overflow."""
    year, month = divmod(today.year * 12 + today.month - 1 - months, 12)
    return date(year, month + 1, 1) + timedelta(days=today.day - 1)


@dataclass(frozen=True)
class Account:
    owed: Decimal | None
    prepaid: Decimal | None
    recent_payment: Decimal | None


def parse_account(body: dict[str, Any], requested: str) -> Account:
    if body.get("consNo") != requested:
        raise InvalidAccount("查询结果与户号不符")
    if not {"totalOweAmt", "prepayAmt"}.issubset(body):
        raise InvalidData("账户金额字段缺失")
    return Account(
        optional_number(body["totalOweAmt"]),
        optional_number(body["prepayAmt"]),
        optional_number(body.get("RecentPay")),
    )


@dataclass(frozen=True)
class Fee:
    name: str
    price: Decimal
    volume: Decimal
    amount: Decimal

    def serialize(self) -> dict[str, str]:
        return {
            "name": self.name,
            "price": str(self.price),
            "volume": str(self.volume),
            "amount": str(self.amount),
        }


@dataclass(frozen=True)
class Bill:
    key: str
    meter: str
    month: str
    start: date
    end: date
    start_reading: Decimal
    end_reading: Decimal
    volume: Decimal
    amount: Decimal
    penalty: Decimal | None
    paid: str | None
    fees: tuple[Fee, ...]

    @property
    def water_signature(self) -> tuple:
        return (self.meter, self.start, self.end, self.start_reading, self.end_reading, self.volume)

    def serialize(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "meter": self.meter,
            "month": self.month,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "start_reading": str(self.start_reading),
            "end_reading": str(self.end_reading),
            "volume": str(self.volume),
            "amount": str(self.amount),
            "penalty": None if self.penalty is None else str(self.penalty),
            "paid": self.paid,
            "fees": [fee.serialize() for fee in self.fees],
        }

    @classmethod
    def restore(cls, item: dict[str, Any]) -> Bill:
        return cls(
            key=text(item["key"]),
            meter=text(item["meter"]),
            month=bill_month(item["month"]),
            start=date.fromisoformat(item["start"]),
            end=date.fromisoformat(item["end"]),
            start_reading=number(item["start_reading"], nonnegative=True),
            end_reading=number(item["end_reading"], nonnegative=True),
            volume=number(item["volume"], nonnegative=True),
            amount=number(item["amount"]),
            penalty=optional_number(item["penalty"]),
            paid=item["paid"],
            fees=tuple(
                Fee(text(f["name"]), number(f["price"]), number(f["volume"]), number(f["amount"]))
                for f in item["fees"]
            ),
        )


def parse_bills(body: dict[str, Any], today: date) -> tuple[Bill, ...]:
    if not isinstance(body.get("reclist"), list):
        raise InvalidData("账单列表缺失")
    result: dict[str, Bill] = {}
    for row in body["reclist"]:
        row = obj(row)
        if not isinstance(row.get("recdetail"), list):
            raise InvalidData("费用明细格式变化")
        fees = tuple(
            Fee(
                text(obj(fee).get("piname")),
                number(fee.get("rddj")),
                number(fee.get("rdsl")),
                number(fee.get("rdje")),
            )
            for fee in row["recdetail"]
        )
        bill = Bill(
            key=digest(text(row.get("rlid"))),
            meter=text(row.get("rlmcode")),
            month=bill_month(row.get("rlmonth")),
            start=reading_date(row.get("rlprdate")),
            end=reading_date(row.get("rlrdate")),
            start_reading=number(row.get("rlscode"), nonnegative=True),
            end_reading=number(row.get("rlecode"), nonnegative=True),
            volume=number(row.get("rlsl"), nonnegative=True),
            amount=number(row.get("rlje")),
            penalty=optional_number(row.get("rlznj")),
            paid=row.get("rlpaidflag"),
            fees=fees,
        )
        if bill.start >= bill.end or bill.end > today:
            raise InvalidData("抄表区间无效")
        if bill.key in result and result[bill.key] != bill:
            raise InvalidData("同一账单标识返回矛盾数据")
        result[bill.key] = bill
    return tuple(result.values())


@dataclass(frozen=True)
class Payment:
    key: str
    when: datetime
    month: str
    paid: Decimal
    settled: Decimal
    prepaid_before: Decimal
    prepaid_change: Decimal
    prepaid_after: Decimal
    penalty: Decimal | None

    @property
    def deduction(self) -> Decimal:
        return max(-self.prepaid_change, ZERO)

    def serialize(self) -> dict[str, str | None]:
        return {
            "key": self.key,
            "when": self.when.isoformat(),
            "month": self.month,
            **{
                key: str(getattr(self, key))
                for key in ("paid", "settled", "prepaid_before", "prepaid_change", "prepaid_after")
            },
            "penalty": None if self.penalty is None else str(self.penalty),
        }

    @classmethod
    def restore(cls, item: dict[str, Any]) -> Payment:
        return cls(
            text(item["key"]),
            datetime.fromisoformat(item["when"]),
            bill_month(item["month"]),
            *(
                number(item[key])
                for key in ("paid", "settled", "prepaid_before", "prepaid_change", "prepaid_after")
            ),
            optional_number(item["penalty"]),
        )


def parse_payments(body: dict[str, Any]) -> tuple[Payment, ...]:
    # jfjl.html treats an absent payment key as an empty result on rtnCode=9999.
    rows = body.get("payment", [])
    if not isinstance(rows, list):
        raise InvalidData("缴费列表格式变化")
    result: dict[str, Payment] = {}
    for row in rows:
        row = obj(row)
        stamp = row.get("pdatetime")
        if not isinstance(stamp, str) or not re.fullmatch(r"\d{14}", stamp):
            raise InvalidData("缴费时间格式变化")
        try:
            when = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=CHINA_TZ)
        except ValueError:
            raise InvalidData("缴费时间无效") from None
        payment = Payment(
            digest(text(row.get("pid"))),
            when,
            bill_month(row.get("pmonth")),
            *(
                number(row.get(key))
                for key in ("ppayment", "pspje", "psavingqc", "psavingbq", "psavingqm")
            ),
            optional_number(row.get("pznj")),
        )
        if (
            payment.prepaid_before + payment.prepaid_change != payment.prepaid_after
            or payment.paid - payment.prepaid_change != payment.settled
        ):
            raise InvalidData("缴费与预存金额无法核对")
        if payment.key in result and result[payment.key] != payment:
            raise InvalidData("同一缴费标识返回矛盾数据")
        result[payment.key] = payment
    return tuple(sorted(result.values(), key=lambda payment: (payment.when, payment.key)))


def parse_analysis(body: dict[str, Any]) -> tuple[tuple[str, Decimal], ...]:
    value = body.get("waterinfo2")
    if not isinstance(value, str):
        raise InvalidData("网站分析字段变化")
    result = []
    for item in value.split("|"):
        if not item:
            continue
        parts = item.split("#")
        if len(parts) != 2:
            raise InvalidData("网站趋势格式变化")
        result.append((bill_month(parts[0]), number(parts[1], nonnegative=True)))
    return tuple(result)


@dataclass(frozen=True)
class Snapshot:
    account: Account
    bills: tuple[Bill, ...]
    payments: tuple[Payment, ...] | None
    analysis: tuple[tuple[str, Decimal], ...] | None
    fetched_at: datetime
    payment_error: str | None = None
    analysis_error: str | None = None

    @property
    def meters(self) -> set[str]:
        return {bill.meter for bill in self.bills}
