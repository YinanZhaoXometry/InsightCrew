"""SEC EDGAR 适配器的测试（BLUEPRINT 18.13）：解析保存的响应、tag 顺序、跨度过滤、
无联系方式不发任何请求、拒绝或畸形的响应抛 SourceUnavailable。全部离线（MockTransport）。"""
from __future__ import annotations

import datetime
from pathlib import Path

import httpx2
import pytest

from insightcrew.adapters.edgar import company_number, download_disclosures, download_statements
from insightcrew.domain.market import SourceUnavailable

FIXTURES = Path(__file__).parent / "fixtures" / "edgar"
CONTACT = "Tester Someone <t@example.com>"
TODAY = datetime.date(2026, 11, 19)


class Replay:
    """按 URL 返回保存的响应，并数一共发了多少个请求。"""

    def __init__(self, folder: Path, status_for: dict[str, int] | None = None,
                 raw: str | None = None):
        self.folder, self.requests = folder, []
        self.status_for, self.raw = status_for or {}, raw

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(str(request.url))
        if self.raw is not None:
            return httpx2.Response(200, text=self.raw)
        status = self.status_for.get(str(request.url), 200)
        name = ("company_tickers.json" if "company_tickers" in str(request.url)
                else "companyfacts_CIK0000320193.json" if "companyfacts" in str(request.url)
                else "submissions_CIK0000320193.json")
        return httpx2.Response(status, text=(self.folder / name).read_text(encoding="utf-8"))


def client(replay: Replay) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(replay))


def rows_by_key(content: dict) -> dict[tuple[str, str], dict]:
    return {(row["concept"], row["period_end"]): row for row in content["rows"]}


# ---- 代码表 ----


def test_company_number_pads_the_cik_to_ten_digits() -> None:
    replay = Replay(FIXTURES)
    with client(replay) as c:
        assert company_number(c, "AAPL", CONTACT) == "0000320193"
        assert company_number(c, "aapl", CONTACT) == "0000320193"      # 大小写不敏感


def test_a_ticker_not_in_the_list_is_not_a_filer() -> None:
    replay = Replay(FIXTURES)
    with client(replay) as c:
        assert company_number(c, "0700.HK", CONTACT) is None


# ---- 年报：过滤、择 tag、去重 ----


def test_statements_keep_only_annual_rows_with_a_year_long_span() -> None:
    replay = Replay(FIXTURES)
    with client(replay) as c:
        result = download_statements(c, "AAPL", CONTACT, today=TODAY)
    assert result is not None
    stem, content = result
    assert stem == "AAPL" and content["source"] == "SEC EDGAR"
    assert content["covers_to"] == "2026-11-19"                        # 下载日
    rows = rows_by_key(content)
    assert ("revenue", "2025-06-30") not in rows                       # 93 天的流量：不取
    assert ("net_income", "2025-06-30") not in rows                    # 10-Q 的行：不取
    assert [row["period_end"] for row in content["rows"]] == sorted(
        row["period_end"] for row in content["rows"])                  # 按财年升序


def test_the_last_of_duplicate_rows_wins_and_restatements_are_kept() -> None:
    replay = Replay(FIXTURES)
    with client(replay) as c:
        result = download_statements(c, "AAPL", CONTACT, today=TODAY)
    assert result is not None
    _, content = result
    rows = {(row["concept"], row["period_end"], row["filed"]): row["value"]
            for row in content["rows"]}
    assert rows[("revenue", "2025-09-30", "2025-11-01")] == 416_100_000_000   # 同键取最后
    assert rows[("revenue", "2024-09-30", "2024-11-01")] == 391_000_000_000   # 不同 filed 的重述都留
    assert rows[("revenue", "2024-09-30", "2025-01-15")] == 391_400_000_000


def test_a_fiscal_year_keeps_the_first_tag_that_reports_it() -> None:
    """2023 财年的收入只由第二个 tag（Revenues）报出；别的单位（CNY）的行一概不算。"""
    replay = Replay(FIXTURES)
    with client(replay) as c:
        result = download_statements(c, "AAPL", CONTACT, today=TODAY)
    assert result is not None
    _, content = result
    rows = rows_by_key(content)
    assert rows[("revenue", "2023-09-30")]["value"] == 383_000_000_000
    assert ("revenue", "2022-09-30") not in rows                        # CNY 单位的行不取


def test_balances_carry_no_start_and_zero_period_days() -> None:
    replay = Replay(FIXTURES)
    with client(replay) as c:
        result = download_statements(c, "AAPL", CONTACT, today=TODAY)
    assert result is not None
    _, content = result
    rows = rows_by_key(content)
    assert rows[("total_assets", "2025-09-30")]["period_days"] == 0
    assert rows[("eps_diluted", "2025-09-30")]["value"] == 6.72         # USD/shares 单位


# ---- 申报文件列表 ----


def test_disclosures_keep_only_the_named_forms_and_build_titles() -> None:
    replay = Replay(FIXTURES)
    with client(replay) as c:
        result = download_disclosures(c, "AAPL", CONTACT,
                                      datetime.date(2024, 1, 1), today=TODAY)
    assert result is not None
    _stem, content = result
    kinds = [item["kind"] for item in content["items"]]
    assert kinds == ["10-K", "8-K", "10-K/A", "10-K", "10-Q"]          # S-1 被排除
    eight_k = content["items"][1]
    assert eight_k["title"] == ("Results of Operations and Financial Condition; "
                                "Financial Statements and Exhibits")
    assert eight_k["ref"] == "0000320193-25-000099"
    assert content["covers_from"] == "2024-08-02"                      # 读到的最老申报日（S-1 不算）
    assert content["covers_to"] == "2026-11-19"


def test_covers_from_never_precedes_the_requested_start() -> None:
    replay = Replay(FIXTURES)
    with client(replay) as c:
        result = download_disclosures(c, "AAPL", CONTACT,
                                      datetime.date(2025, 1, 1), today=TODAY)
    assert result is not None
    _, content = result
    assert content["covers_from"] == "2025-01-01"                      # 最老申报早于起点：用起点


# ---- 不发请求与失败 ----


def test_no_request_is_made_without_a_contact() -> None:
    replay = Replay(FIXTURES)
    with client(replay) as c:
        for call in (lambda: company_number(c, "AAPL", ""),
                     lambda: download_statements(c, "AAPL", ""),
                     lambda: download_disclosures(c, "AAPL", "", datetime.date(2025, 1, 1))):
            with pytest.raises(ValueError, match="IC_SEC_CONTACT"):
                call()
    assert replay.requests == []                                       # 一个请求都没发


def test_every_request_carries_the_contact_in_the_user_agent() -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers.get("User-Agent", ""))
        return httpx2.Response(200, text=(FIXTURES / "company_tickers.json").read_text(encoding="utf-8"))

    with httpx2.Client(transport=httpx2.MockTransport(handler)) as c:
        company_number(c, "AAPL", CONTACT)
    assert seen and all("InsightCrew/" in ua and CONTACT in ua for ua in seen)


def test_a_refused_response_raises_with_the_status() -> None:
    replay = Replay(FIXTURES, status_for={"https://www.sec.gov/files/company_tickers.json": 403})
    with client(replay) as c, pytest.raises(SourceUnavailable, match="403"):
        company_number(c, "AAPL", CONTACT)


def test_a_malformed_response_raises_and_writes_nothing() -> None:
    replay = Replay(FIXTURES, raw="<html>not json</html>")
    with client(replay) as c, pytest.raises(SourceUnavailable):
        company_number(c, "AAPL", CONTACT)


def test_statements_of_a_non_filer_are_none() -> None:
    with client(Replay(FIXTURES)) as c:
        assert download_statements(c, "0700.HK", CONTACT) is None
        assert download_disclosures(c, "0700.HK", CONTACT, datetime.date(2025, 1, 1)) is None
