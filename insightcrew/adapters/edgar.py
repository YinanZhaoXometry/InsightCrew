"""SEC EDGAR 的下载适配器（BLUEPRINT 18.12）：本文件是唯一知道 SEC 端点的代码。

三个函数都在调用方给的 httpx2.Client 上同步请求，每个请求都带自报的联系方式
（SEC 的要求）。没有联系方式就一个请求都不发（抛 ValueError，由 fetch 层先判断并说明）。
下载结果直接转成 statements / disclosures JSON 文件的内容（18.7 的格式），由 fetch 落盘；
这里不做任何文件 I/O。失败（HTTP 非 200、响应不是 JSON）抛 SourceUnavailable，状态码在消息里。

XBRL 的行规则（18.12）：只留 form 以 10-K、20-F、40-F 开头的行；存量（无 start）保留，
流量要求 end − start 在 350 到 380 天；一个概念按 tag 顺序取第一个报出该财年的 tag，
绝不跨 tag 相加；同一 (concept, period_end, filed) 出现多次取最后一个。
"""
from __future__ import annotations

import datetime
from typing import Any

from insightcrew import __version__

from ..domain.market import SourceUnavailable

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# 概念 →（按序尝试的 XBRL tag，单位）。一个概念只以表里的单位读取；换算过单位的行不取，
# 该概念因此缺席，运行时成为一条不可用事实（18.12）。
_CONCEPTS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("revenue", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                 "SalesRevenueNet"), "USD"),
    ("net_income", ("NetIncomeLoss",), "USD"),
    ("eps_diluted", ("EarningsPerShareDiluted",), "USD/shares"),
    ("operating_cash_flow", ("NetCashProvidedByUsedInOperatingActivities",), "USD"),
    ("total_assets", ("Assets",), "USD"),
    ("total_liabilities", ("Liabilities",), "USD"),
)

_ANNUAL_FORMS = ("10-K", "20-F", "40-F")            # 前缀匹配，含 /A 重述
_FILING_FORMS = ("8-K", "10-Q", "10-K", "20-F", "40-F", "6-K")   # 前缀匹配，含 /A

# 8-K 条目编号的官方名称（SEC 自己的说法）；未知的编号打印为 Item <number>。
_ITEM_NAMES = {
    "1.01": "Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Acceleration of Direct Financial Obligations",
    "3.01": "Notice of Delisting or Failure to Satisfy a Continued Listing Rule",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure of Directors or Certain Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}
_FORM_NAMES = {"10-K": "Annual report", "10-Q": "Quarterly report", "8-K": "Current report",
               "20-F": "Annual report of a foreign private issuer",
               "40-F": "Annual report (Canadian issuers)", "6-K": "Report of foreign private issuer"}


def company_number(client, ticker: str, contact: str) -> str | None:
    """代码的十位 CIK；不在 SEC 名单里返回 None：这家公司不是 SEC 申报人。"""
    data = _get(client, _TICKERS_URL, contact)
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker.upper():
            return str(entry["cik_str"]).zfill(10)
    return None


def download_statements(client, ticker: str, contact: str,
                        today: datetime.date | None = None) -> tuple[str, dict[str, Any]] | None:
    """下载一家公司的年度报表行，转成 statements JSON 的内容。

    不是 SEC 申报人返回 None（不写任何文件）。filed 晚于今天的行不会存在（数据源给的）。
    """
    cik = company_number(client, ticker, contact)
    if cik is None:
        return None
    data = _get(client, _FACTS_URL.format(cik=cik), contact)
    rows = _annual_rows(data.get("facts", {}).get("us-gaap", {}))
    return ticker.upper(), {
        "source": "SEC EDGAR",
        "currency": "USD",
        "covers_to": (today or datetime.date.today()).isoformat(),
        "rows": rows,
    }


def download_disclosures(client, ticker: str, contact: str, start: datetime.date,
                         today: datetime.date | None = None) -> tuple[str, dict[str, Any]] | None:
    """下载一家公司的申报文件列表（只读 recent），转成 disclosures JSON 的内容。"""
    cik = company_number(client, ticker, contact)
    if cik is None:
        return None
    data = _get(client, _SUBMISSIONS_URL.format(cik=cik), contact)
    recent = data.get("filings", {}).get("recent", {})
    items: list[dict[str, Any]] = []
    oldest: datetime.date | None = None
    for form, filed, numbers, accession in zip(
            recent.get("form", ()), recent.get("filingDate", ()),
            recent.get("items", ()), recent.get("accessionNumber", ()), strict=True):
        if not form.startswith(_FILING_FORMS):
            continue
        day = _day(filed)
        if day is None:
            continue
        items.append({"published": filed, "kind": form,
                      "title": _title(form, numbers), "ref": accession})
        oldest = day if oldest is None or day < oldest else oldest
    return ticker.upper(), {
        "source": "SEC EDGAR",
        "covers_from": max(start, oldest or start).isoformat(),
        "covers_to": (today or datetime.date.today()).isoformat(),
        "items": items,
    }


# ---- 内部 ----


def _get(client, url: str, contact: str) -> Any:
    if not contact:
        raise ValueError("没有联系方式（IC_SEC_CONTACT），不访问 SEC")
    response = client.get(url, headers={"User-Agent": f"InsightCrew/{__version__} {contact}",
                                        "Accept": "application/json"})
    if response.status_code != 200:
        raise SourceUnavailable(f"SEC 请求失败：HTTP {response.status_code}（{url}）")
    try:
        return response.json()
    except ValueError as e:
        raise SourceUnavailable(f"SEC 响应不是 JSON：{url}") from e


def _annual_rows(us_gaap: dict[str, Any]) -> list[dict[str, Any]]:
    """把 companyfacts 的 us-gaap 部分转成 18.7 的行：过滤、择 tag、去重，按财年升序输出。

    “一个财年由第一个报出它的 tag 提供”按概念分别判定：不同概念共用同一个期末日。
    """
    years: dict[tuple[str, datetime.date], dict[datetime.date, dict[str, Any]]] = {}
    for concept, tags, unit in _CONCEPTS:
        for tag in tags:
            entries = us_gaap.get(tag, {}).get("units", {}).get(unit)
            if not entries:
                continue
            for entry in entries:
                if not str(entry.get("form", "")).startswith(_ANNUAL_FORMS):
                    continue
                period_days = _span(entry)
                if period_days is None:
                    continue
                period_end = _day(entry.get("end"))
                filed = _day(entry.get("filed"))
                if period_end is None or filed is None:
                    continue
                key = (concept, period_end)
                if key in years and tag != _tag_of(years[key]):
                    continue        # 这个概念的该财年已由更早尝试的 tag 提供，绝不跨 tag 合并
                years.setdefault(key, {})[filed] = {
                    "concept": concept, "period_end": period_end.isoformat(),
                    "period_days": period_days, "value": entry["val"],
                    "filed": filed.isoformat(), "form": entry["form"],
                    "_tag": tag}
    rows = [row for key in sorted(years, key=lambda k: (k[1], k[0]))
            for _filed, row in sorted(years[key].items())]
    for row in rows:
        del row["_tag"]
    return rows


def _tag_of(year_rows: dict[datetime.date, dict[str, Any]]) -> str:
    return next(iter(year_rows.values()))["_tag"]


def _span(entry: dict[str, Any]) -> int | None:
    """行的跨度：存量（无 start）为 0；流量要求 350 到 380 天，否则不取（季度等）。"""
    start = _day(entry.get("start"))
    end = _day(entry.get("end"))
    if start is None:
        return 0
    if end is None:
        return None
    days = (end - start).days
    return days if 350 <= days <= 380 else None


def _day(text: Any) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def _title(form: str, item_numbers: Any) -> str:
    """标题：8-K 用 SEC 自己的条目名称（分号连接）；其余用报表的英文名。"""
    base = form.removesuffix("/A")
    if base == "8-K" and isinstance(item_numbers, (list, tuple)):
        return "; ".join(_ITEM_NAMES.get(str(n), f"Item {n}") for n in item_numbers)
    return _FORM_NAMES.get(base, form)
