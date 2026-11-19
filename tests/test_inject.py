"""WP37 的注入评测测试：版本 2.2 的第三块（BLUEPRINT 19.16）。

六个故障（组合状态翻转触发 max_position_pct 与 sell_requires_position、新闻 claim
引用不存在的宏观事实、情绪信号引用不存在的帖子、情绪分落在情感带之外、教训文字里
带数字）都必须被发现，两个有效变体（held 小仓位组合下留有余量的 Buy、恰好压在
neutral 带上界的情绪分）都不得误报；整份注入评测退出码 0。
"""
from __future__ import annotations

import pytest

from insightcrew.evals import inject

_CHECKS = ("max_position_pct", "sell_requires_position", "claim_cites_known_fact",
           "sentiment_signal_cites_known_post", "sentiment_score_matches_band",
           "lesson_text_has_no_numbers")


def test_the_v22_block_detects_every_fault_and_raises_no_false_alarm(
        capsys: pytest.CaptureFixture[str]) -> None:
    facts, values, specs = inject._load_values()
    assert inject._v22_block(facts, values, specs) is True
    out = capsys.readouterr().out
    for check in _CHECKS:
        assert check in out
    assert "版本 2.2 故障 6/6 被发现" in out and "有效变体 0/2 误报" in out
    assert "漏报" not in out and "误报（" not in out and "不干净" not in out


def test_the_full_inject_evaluation_exits_zero() -> None:
    assert inject.main() == 0
