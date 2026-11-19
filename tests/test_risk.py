"""RiskView 契约的测试（BLUEPRINT 19.7、19.10）：字段照声明收，stance 只收三个值，
多出的字段被拒，可空字段按声明行事（可空但无默认值，省略即错），cites 收敛成元组。
提示词与角色声明不在本包：角色由 WP31 声明，辩论进流程是 WP32。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from insightcrew.domain.risk import RiskView

FULL = {
    "stance": "conservative",
    "critique": "仓位相对波动偏大，止损放在近期波动之内。",
    "size_pct": 5.0,
    "stop": 92.0,
    "target": None,
    "cites": ["close", "atr_14"],
    "rebuttal": "",
}


def test_accepts_a_full_view_with_keyword_arguments():
    view = RiskView(stance="aggressive", critique="上行留了太多在桌上。", size_pct=8.0,
                    stop=91.5, target=130.0, cites=("close", "atr_14"),
                    rebuttal="保守方看漏了趋势已反转的事实。")
    assert view.stance == "aggressive"
    assert view.size_pct == 8.0 and view.stop == 91.5 and view.target == 130.0
    assert view.cites == ("close", "atr_14")
    assert view.rebuttal == "保守方看漏了趋势已反转的事实。"


def test_rejects_an_unknown_stance():
    with pytest.raises(ValidationError) as e:
        RiskView.model_validate({**FULL, "stance": "bold"})
    assert "stance" in str(e.value) and "bold" in str(e.value)


def test_rejects_an_undeclared_field():
    with pytest.raises(ValidationError) as e:
        RiskView.model_validate({**FULL, "confidence": "high"})
    assert "confidence" in str(e.value)


def test_nullable_fields_take_none_and_have_no_default():
    view = RiskView(stance="neutral", critique="收益与风险相称。", size_pct=None, stop=None,
                    target=None, cites=(), rebuttal="")
    assert view.size_pct is None and view.stop is None and view.target is None
    # 声明里没有默认值：字段仍然必填，省略是错误，不会被悄悄补成 null
    assert RiskView.model_fields["size_pct"].is_required()
    assert RiskView.model_fields["stop"].is_required()
    assert RiskView.model_fields["target"].is_required()
    with pytest.raises(ValidationError):
        RiskView.model_validate(
            {"stance": "neutral", "critique": "c", "cites": [], "rebuttal": ""})


def test_cites_is_a_tuple():
    view = RiskView.model_validate(FULL)
    assert isinstance(view.cites, tuple)
    assert view.cites == ("close", "atr_14")
