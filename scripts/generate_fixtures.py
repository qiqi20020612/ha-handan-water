"""Generate public protocol fixtures using entirely invented household data."""

import json
from pathlib import Path


def generate(root: Path) -> None:
    def save(number: int, service: str, body: dict) -> None:
        payload = {
            "result": "ok",
            "data": {
                "head": {
                    "servCode": service,
                    "extend": "",
                    "msgId": "",
                    "msgTime": "20261019120000",
                    "source": "",
                    "version": "1.0.1",
                    "desIfno": "",
                },
                "body": body,
            },
        }
        (root / f"response_{number}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )

    account = {
        "bc": "",
        "totalPenalty": "0",
        "RecentWater": "2",
        "RecentPay": "12",
        "recordCount": "0",
        "capitalNo": "",
        "consName": "DEMO_NAME_001",
        "opfid": "DEMO_OPFID_001",
        "consType": "GR",
        "orgNo": "DEMO_ORGID_001",
        "totalOweAmt": "0",
        "dkBs": "0",
        "averagePay": "15",
        "prepayAmt": "0",
        "addr": "DEMO_ADDRESS_001",
        "rtnCode": "9999",
        "orgName": "DEMO_ORGNAME_001",
        "ysrs": "4",
        "totalRcvblAmt": "0",
        "rtnMsg": "未欠费",
        "sftel": "DEMO_PHONE_001",
        "consNo": "DEMO_ACCOUNT_001",
        "bcname": "",
        "totalRcvedAmt": "0",
        "phone": "",
        "jtsyl_2": "未达到",
        "jtsyl_1": "余量100",
        "acctOrgNo": "",
    }
    for number in (16, 37):
        save(number, "200001", account)

    alternate = {
        key: account[key]
        for key in (
            "totalPenalty",
            "orgName",
            "recordCount",
            "totalRcvblAmt",
            "capitalNo",
            "consName",
            "rtnMsg",
            "consNo",
            "consType",
            "orgNo",
            "totalRcvedAmt",
            "totalOweAmt",
            "prepayAmt",
            "addr",
            "rtnCode",
        )
    }
    alternate["acctOrgNo"] = "DEMO_ORGID_003"
    alternate["rcvblDet"] = [
        {
            key: ""
            for key in (
                "rcvblYm",
                "orgName",
                "consName",
                "rcvedAmt",
                "consNo",
                "oweAmt",
                "extend",
                "orgNo",
                "tPq",
                "rcvblAmt",
                "rcvblPenalty",
                "acctOrgNo",
                "rcvblAmtId",
            )
        }
    ]
    save(38, "200001", alternate)

    dates = [
        "2025/09/10",
        "2025/11/10",
        "2026/01/12",
        "2026/03/12",
        "2026/05/12",
        "2026/07/14",
        "2026/09/14",
    ]
    volumes = ((3, 5, 7, 4, 6, 2), (2, 1, 3, 2, 0, 1))
    readings = [1000, 200]
    groups = []
    for period in range(6):
        bills = []
        for meter in range(2):
            quantity = volumes[meter][period]
            start = readings[meter]
            readings[meter] += quantity
            bills.append(
                {
                    "rlid": f"DEMO_RECORD_{(5 - period) * 2 + meter + 1:03d}",
                    "rlmcode": f"DEMO_METER_{meter + 1:03d}",
                    "rlje": quantity * 5,
                    "rlprdate": dates[period],
                    "rlrdate": dates[period + 1],
                    "recdetail": [
                        {"rdje": quantity * price, "piname": name, "rddj": price, "rdsl": quantity}
                        for name, price in (
                            ("水费一阶", 3.5),
                            ("污水处理费", 1),
                            ("不征税水费", 0.5),
                        )
                    ],
                    "rlmonth": dates[period + 1][:7].replace("/", "."),
                    "rlscode": start,
                    "rlpaidflag": "Y",
                    "rlznj": 1 if period == 2 else 0,
                    "rlsl": quantity,
                    "rlecode": readings[meter],
                }
            )
        groups.append(bills)
    save(
        27,
        "200005",
        {
            "totalOweAmt": 0,
            "reclist": [bill for bills in reversed(groups) for bill in bills],
            "rceAmt": "0",
            "misaving": 0,
        },
    )

    transactions = [
        ("20251110090000", 4, 0, None),
        ("20251115100000", 21, 25, 0),
        ("20260105090000", 6, 0, None),
        ("20260115100000", 24, 30, 1),
        ("20260305090000", 10, 0, None),
        ("20260315100000", 40, 50, 2),
        ("20260515100000", 30, 30, 3),
        ("20260705090000", 5, 0, None),
        ("20260715100000", 25, 30, 4),
        ("20260902090000", 1, 0, None),
        ("20260905090000", 2, 0, None),
        ("20260915100000", 12, 15, 5),
    ]
    payments = []
    balance = 0
    for index, (stamp, paid, settled, group) in enumerate(transactions):
        change = paid - settled
        item = {
            "pdatetime": stamp,
            "pmonth": f"{stamp[:4]}.{stamp[4:6]}",
            "psavingbq": change,
            "pznj": 0,
            "psavingqc": balance,
            "pspje": settled,
            "ppayment": paid,
            "pid": f"DEMO_TRANSACTION_{12 - index:03d}",
            "chargetype": "示例渠道",
            "psavingqm": balance + change,
            "invflag": "N",
        }
        if group is not None:
            item["reclist"] = [
                {
                    key: bill[key]
                    for key in (
                        "rlje",
                        "rlmonth",
                        "rlscode",
                        "rlznj",
                        "rlsl",
                        "rlecode",
                    )
                }
                for bill in groups[group]
            ]
        balance += change
        payments.append(item)
    save(
        52,
        "200008",
        {
            "extend": "",
            "payment": list(reversed(payments)),
            "rtnMsg": "查询成功",
            "rtnCode": "9999",
        },
    )
    trend = "".join(f"{bills[0]['rlmonth']}#{bills[0]['rlsl']}|" for bills in reversed(groups))
    save(
        58,
        "200020",
        {
            "orgName": "DEMO_ORGNAME_001",
            "orgNo": "DEMO_ORGID_001",
            "historyRank": "1000",
            "rtnMsg": "交易成功",
            "waterinfo3": "",
            "waterinfo2": trend,
            "rtnCode": "9999",
            "waterinfo1": "DEMO_ACCOUNT|-1|DEMO_ACCOUNT|-1|2026.09|2|50.00%|",
        },
    )


if __name__ == "__main__":
    generate(Path(__file__).resolve().parents[1] / "tests/fixtures")
