"""数据源链的测试（BLUEPRINT 19.12、19.18 WP30）：按序尝试与回退、错误归类、
全败时逐家列出原因，以及 scrub 的脱敏（密钥参数、以 KEY/TOKEN 结尾的环境变量）。"""
from __future__ import annotations

import pytest

from insightcrew.adapters.vendors import first_ok, scrub
from insightcrew.domain.market import NoData, NotConfigured, NotCovered, SourceUnavailable

# ---- first_ok：顺序与回退 ----


def test_returns_the_first_vendor_without_calling_the_rest() -> None:
    calls: list[str] = []

    def alpha() -> int:
        calls.append("alpha")
        return 1

    def beta() -> int:
        calls.append("beta")
        return 2

    assert first_ok((("alpha", alpha), ("beta", beta))) == ("alpha", 1)
    assert calls == ["alpha"]


def test_an_unavailable_vendor_moves_on_to_the_next() -> None:
    def yahoo_side() -> int:
        raise SourceUnavailable("HTTP 429")

    def eastmoney_side() -> int:
        return 7

    assert first_ok((("yahoo", yahoo_side), ("eastmoney", eastmoney_side))) == ("eastmoney", 7)


def test_no_data_not_covered_and_not_configured_all_move_on() -> None:
    def empty() -> int:
        raise NoData("没有任何条目")

    def uncovered() -> int:
        raise NotCovered("没有这个序列")

    def keyless() -> int:
        raise NotConfigured("没有密钥")

    def works() -> int:
        return 3

    chain = (("google_rss", empty), ("fred", uncovered), ("stocktwits", keyless), ("reddit", works))
    assert first_ok(chain) == ("reddit", 3)


def test_every_vendor_failing_lists_each_name_and_reason() -> None:
    def refused() -> int:
        raise SourceUnavailable("HTTP 429（https://x.test/a）")

    def empty() -> int:
        raise NoData("没有任何条目")

    def keyless() -> int:
        raise NotConfigured("没有配置密钥")

    with pytest.raises(SourceUnavailable) as caught:
        first_ok((("google_rss", refused), ("yahoo_rss", empty), ("fred", keyless)))
    message = str(caught.value)
    for part in ("google_rss", "yahoo_rss", "fred",
                 "HTTP 429", "没有任何条目", "没有配置密钥"):
        assert part in message
    assert message.index("google_rss") < message.index("yahoo_rss") < message.index("fred")


def test_an_empty_chain_raises_source_unavailable() -> None:
    with pytest.raises(SourceUnavailable, match="没有任何数据源"):
        first_ok(())


def test_an_untyped_error_is_not_swallowed() -> None:
    """链只消化"这一源不行"；代码错误（如 ValueError）原样抛出，不悄悄换源。"""

    def broken() -> int:
        raise ValueError("畸形响应")

    def works() -> int:
        return 1

    with pytest.raises(ValueError, match="畸形响应"):
        first_ok((("a", broken), ("b", works)))


# ---- scrub：密钥参数与环境变量值 ----


def test_scrub_removes_query_parameters_named_like_keys_and_tokens() -> None:
    text = ("FRED 请求失败：HTTP 500（https://api.stlouisfed.org/fred/series/observations"
            "?series_id=CPIAUCSL&api_key=abc123def&file_type=json）")
    out = scrub(text)
    assert "api_key" not in out and "abc123def" not in out
    assert "series_id=CPIAUCSL" in out and "file_type=json" in out


def test_scrub_hits_every_url_and_keeps_plain_parameters() -> None:
    text = ("先试 https://x.test/a?token=zzz&q=1，再试 "
            "https://y.test/b?access-token=yyy&lang=zh，最后 https://z.test/c?apikey=xxx&n=2")
    out = scrub(text)
    assert "token" not in out and "access-token" not in out and "apikey" not in out
    assert "https://x.test/a?q=1" in out
    assert "https://y.test/b?lang=zh" in out
    assert "https://z.test/c?n=2" in out


def test_scrub_masks_the_value_of_any_environment_variable_ending_in_key(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IC_FAKE_SNEAKY_KEY", "kjwi8273hjadf")
    out = scrub("配置里读到的密钥是 kjwi8273hjadf，请检查。")
    assert "kjwi8273hjadf" not in out
    assert "***" in out


def test_scrub_masks_the_value_of_any_environment_variable_ending_in_token(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IC_FAKE_BEARER_TOKEN", "tok-9823742-xyz")
    out = scrub("tok-9823742-xyz 出现在了消息里")
    assert "tok-9823742-xyz" not in out
    assert "***" in out


def test_scrub_leaves_text_without_secrets_alone() -> None:
    text = "东方财富公告请求失败：HTTP 502（第 3 页）"
    assert scrub(text) == text


def test_the_chain_failure_message_is_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    """全败消息里带着各家的 URL：密钥参数与密钥值都到不了消息里。"""
    monkeypatch.setenv("IC_FAKE_SNEAKY_KEY", "verysecretkey123")

    def fred_side() -> int:
        raise SourceUnavailable("https://api.stlouisfed.org/fred/series/observations"
                                "?series_id=CPIAUCSL&api_key=verysecretkey123&file_type=json")

    with pytest.raises(SourceUnavailable) as caught:
        first_ok((("fred", fred_side),))
    message = str(caught.value)
    assert "verysecretkey123" not in message
    assert "api_key" not in message
    assert "series_id=CPIAUCSL" in message
