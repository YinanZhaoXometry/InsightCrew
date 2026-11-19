"""公告端口的测试：窗口边界（两端含）、无日期行丢弃计数、发布日之前不返回、
窗口出界与缺文件是 NotCovered、坏文件是 SourceUnavailable、文件顺序保持
（BLUEPRINT 18.13 第三行；排序、截断与 D1 编号是 evidence 阶段的职责，见 18.8）。"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from insightcrew.adapters.disclosures_json import JsonDisclosures
from insightcrew.domain.disclosures import DisclosureSet
from insightcrew.domain.market import NotCovered, SourceUnavailable

ROOT = Path(__file__).resolve().parent.parent
PORT = JsonDisclosures(ROOT / "data" / "market", "synthetic")
GENERATOR = ROOT / "data" / "make_disclosures.py"
AS_OF = date(2026, 9, 30)


def window(as_of: date, days: int = 90):
    return as_of - timedelta(days=days), as_of


def refs(items) -> list[str]:
    return [item.ref for item in items]


# ---- 生成器 ----


def test_generator_is_deterministic():
    first = subprocess.run([sys.executable, str(GENERATOR)], check=True, capture_output=True)
    files = {p.name: p.read_bytes() for p in sorted((ROOT / "data/market/synthetic/disclosures").iterdir())}
    subprocess.run([sys.executable, str(GENERATOR)], check=True, capture_output=True)
    assert {p.name: p.read_bytes() for p in sorted((ROOT / "data/market/synthetic/disclosures").iterdir())} == files
    assert set(files) == {"ACME.json", "0000.HK.json"}
    assert first.returncode == 0


# ---- 窗口与状态（18.7 的验收数字）----


def test_acme_on_2026_09_30_with_a_90_day_window_holds_three_items_and_one_dropped():
    ds = PORT.published("ACME", *window(AS_OF))
    assert refs(ds.items) == ["ACME-0009", "ACME-0010", "ACME-0011"]     # 08-06、08-14、09-22
    assert ds.dropped == 1                                              # 无日期的那一条
    assert ds.source == "合成数据"
    assert all(isinstance(item.published, date) for item in ds.items)


def test_0000hk_on_2026_06_30_holds_none():
    start, as_of = window(date(2026, 6, 30))
    ds = PORT.published("0000.HK", start, as_of)
    assert ds.items == () and ds.dropped == 0                           # 窗口里没有公告：empty 状态


def test_acme_on_2025_03_31_is_not_covered():
    with pytest.raises(NotCovered):
        PORT.published("ACME", *window(date(2025, 3, 31)))              # 窗口早于 covers_from


# ---- 窗口边界与顺序 ----


def test_both_window_ends_are_inclusive():
    start = date(2026, 8, 6)                                            # 恰好是 0009 的发布日
    ds = PORT.published("ACME", start, date(2026, 9, 22))               # 恰好是 0011 的发布日
    assert refs(ds.items) == ["ACME-0009", "ACME-0010", "ACME-0011"]


def test_an_item_is_not_returned_before_its_date():
    ds = PORT.published("ACME", *window(date(2026, 10, 1)))
    assert "ACME-0013" not in refs(ds.items)                            # 10-15 发布，10-01 看不到
    later = PORT.published("ACME", *window(date(2026, 10, 15)))
    assert "ACME-0013" in refs(later.items)


def test_items_keep_file_order_within_the_window():
    ds = PORT.published("ACME", date(2025, 1, 5), date(2026, 12, 31))
    assert refs(ds.items) == [f"ACME-{n:04d}" for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13)]


# ---- 错误分类 ----


def test_a_date_after_the_cover_is_not_covered():
    with pytest.raises(NotCovered):
        PORT.published("ACME", *window(date(2027, 6, 30)))              # as_of 晚于 covers_to


def test_a_ticker_without_a_file_is_not_covered():
    with pytest.raises(NotCovered):
        PORT.published("THIN", *window(AS_OF))


def test_covers_are_read_from_the_file(tmp_path: Path):
    (tmp_path / "synthetic" / "disclosures").mkdir(parents=True)
    (tmp_path / "synthetic" / "disclosures" / "ACME.json").write_text(
        json.dumps({"source": "测试", "covers_from": "2026-06-01", "covers_to": "2026-12-31",
                    "items": [{"published": "2026-08-06", "kind": "季度业绩",
                               "title": "2026 年第二季度业绩公告", "ref": "ACME-0009"}]},
                   ensure_ascii=False), encoding="utf-8")
    port = JsonDisclosures(tmp_path, "synthetic")
    ok = port.published("ACME", date(2026, 6, 1), date(2026, 9, 30))
    assert refs(ok.items) == ["ACME-0009"]
    with pytest.raises(NotCovered):                                     # start 早于 covers_from
        port.published("ACME", date(2026, 5, 31), date(2026, 9, 30))


def test_broken_json_is_source_unavailable(tmp_path: Path):
    (tmp_path / "synthetic" / "disclosures").mkdir(parents=True)
    (tmp_path / "synthetic" / "disclosures" / "ACME.json").write_text("{ 不是 JSON", encoding="utf-8")
    with pytest.raises(SourceUnavailable):
        JsonDisclosures(tmp_path, "synthetic").published("ACME", *window(AS_OF))


def test_unknown_field_is_refused(tmp_path: Path):
    (tmp_path / "synthetic" / "disclosures").mkdir(parents=True)
    item = {"published": "2026-08-06", "kind": "季度业绩", "title": "t", "ref": "r", "extra": 1}
    (tmp_path / "synthetic" / "disclosures" / "ACME.json").write_text(
        json.dumps({"source": "测试", "covers_from": "2025-01-01", "covers_to": "2026-12-31",
                    "items": [item]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SourceUnavailable):
        JsonDisclosures(tmp_path, "synthetic").published("ACME", *window(AS_OF))


def test_a_missing_field_is_source_unavailable(tmp_path: Path):
    (tmp_path / "synthetic" / "disclosures").mkdir(parents=True)
    item = {"published": "2026-08-06", "kind": "季度业绩"}              # 缺 title 与 ref
    (tmp_path / "synthetic" / "disclosures" / "ACME.json").write_text(
        json.dumps({"source": "测试", "covers_from": "2025-01-01", "covers_to": "2026-12-31",
                    "items": [item]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SourceUnavailable):
        JsonDisclosures(tmp_path, "synthetic").published("ACME", *window(AS_OF))


def test_the_shape_of_the_returned_set():
    ds = PORT.published("ACME", *window(AS_OF))
    assert isinstance(ds, DisclosureSet)
    first = ds.items[0]
    assert first.kind == "季度业绩" and first.title == "2026 年第二季度业绩公告"
    assert first.ref == "ACME-0009"
