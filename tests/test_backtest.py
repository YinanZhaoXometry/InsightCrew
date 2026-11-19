"""回测的测试（BLUEPRINT 19.18 WP35）：剧本化的三个日期网格——先结算后分析、
不变量 20（晚于当日的教训不可见）、同 --run-id 续跑不做新事、批次预算停住后可续、
真实模型没有 --yes 拒绝、坏范围与坏代码退出码 2。

剧本模型按格子自洽：从提示词里的 ledger/facts 取当日事实，给出与账本一致的回答
（静态回答过不了不同日期的审计，这正是回测的作答必须随日期走的原因）。不联网。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from insightcrew.adapters.decision_log import DecisionLog
from insightcrew.app import backtest as backtest_cmd
from insightcrew.app.settings import load
from insightcrew.app.wiring import ROOT
from insightcrew.domain.memory import DecisionRecord, OutcomeRecord
from insightcrew.domain.types import Action, Rating

GRID = ("2026-08-03", "2026-08-10", "2026-08-17")     # ACME 的交易日，every=5


def _fact_values(prompt: str) -> dict[str, float]:
    block = prompt.split('<artifact key="ledger/facts">', 1)[1].split("</artifact>", 1)[0]
    return {row["id"]: float(row["value"]) for row in map(json.loads, block.strip().splitlines())}


def _reply(role_name: str, facts: dict[str, float]) -> dict:
    """按角色给一份契约合规的回答。只有 technical 与 trader 读 ledger/facts（需要当日数值），
    其余角色的回答静态成立：引用的 ID 都在技术分析的 claim 里。"""
    if role_name == "reflector":                       # 复盘员不读账本，教训固定
        return {"text": "回调不破均线时再进场，宁可错过也不追高",
                "cites": ["return_pct", "hit"]}
    if role_name == "technical":
        close, atr, sma = facts["close"], facts["atr_14"], facts["sma_20"]
        return {"claims": [
            {"fact": "close", "value": close, "reading": "收盘价站上均线，趋势向上"},
            {"fact": "sma_20", "value": sma, "reading": "二十日均线之上，多头排列"},
            {"fact": "atr_14", "value": atr, "reading": "日均真实波幅中等"}],
            "narrative": "趋势与动能同向，顺势做多；仓位与止损按风控约束执行。"}
    if role_name in ("bull", "bear"):
        return {"points": [
            {"text": "价格站上二十日均线，趋势结构完整", "cites": ["close", "sma_20"]},
            {"text": "波动以 ATR 衡量处于中等水平", "cites": ["atr_14"]}],
            "rebuttal": "对方担心的风险由止损与仓位控制。"}
    if role_name == "manager":
        return {"rating": "Buy", "horizon_days": 20, "points": [
            {"text": "顺势结构未破，持有期内有望沿均线上行", "cites": ["close", "sma_20"]}],
            "invalidation": ["收盘跌破止损位", "跌破二十日均线且无法收复"],
            "summary": "趋势与动能同向，顺势做多。"}
    if role_name == "trader":
        close, atr = facts["close"], facts["atr_14"]
        return {"action": "Buy", "entry": close, "stop": round(close - 2.5 * atr, 4),
                "target": round(close + 4 * atr, 4), "size_pct": 5.0, "horizon_days": 20,
                "cites": ["close", "atr_14"], "rationale": "止损两倍半 ATR，收益风险比一点六"}
    if role_name.startswith("judge."):
        return {"accept": True, "target": "none", "critique": "引用与账本一致，方案在限内。",
                "cites": []}
    raise AssertionError(f"剧本没有为角色 {role_name} 写回答")


class _GridModels:
    """Models 端口：每个角色拿到一个读提示词、按当日事实作答的 FunctionModel。"""

    def for_role(self, role):
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            first = messages[0]
            assert isinstance(first, ModelRequest)
            prompt = "".join(p.content for p in first.parts
                             if isinstance(p, UserPromptPart) and isinstance(p.content, str))
            facts = _fact_values(prompt) if "ledger/facts" in prompt else {"close": 1.0}
            reply = _reply(role.name, facts)
            part = (ToolCallPart(info.output_tools[0].name, reply) if info.output_tools
                    else TextPart(json.dumps(reply, ensure_ascii=False)))
            return ModelResponse(parts=[part],
                                 usage=RequestUsage(input_tokens=1000, output_tokens=300),
                                 model_name="grid-script")

        return FunctionModel(respond, model_name="grid-script")


def _settings(tmp_path: Path, **extra: object):
    return load(ROOT, force_offline=True,
                memory={"path": str(tmp_path / "decisions.jsonl"), "holding_days": 3,
                        "enabled": True}, **extra)


def _script_models(monkeypatch, *modules) -> None:
    """换掉模型来源：wiring._models 供 open_run（分析格子）用，各命令模块自己的绑定也要换。"""
    from insightcrew.app import wiring

    def _fake(settings, scenario, replay):
        return _GridModels(), "scripted", {"quick": "scripted", "deep": "scripted"}

    monkeypatch.setattr(wiring, "_models", _fake)
    for module in modules:
        monkeypatch.setattr(module, "_models", _fake)


def _board(batch_dir: Path, run_id: str) -> dict:
    return json.loads((batch_dir / run_id / "board.json").read_text(encoding="utf-8"))


def _seed(ticker: str = "ACME", as_of: str = GRID[0]) -> DecisionRecord:
    return DecisionRecord(kind="decision", run_id="seed-1", ticker=ticker, as_of=as_of,
                          rating=Rating.BUY, action=Action.BUY, entry=208.85, stop=202.1,
                          target=221.0, size_pct=5.0, horizon_days=20)


# ---- 主路径：三个日期，先结算后分析，续跑不做新事 ----


def test_scripted_three_date_backtest_settles_records_and_resumes(tmp_path, monkeypatch, capsys):
    from insightcrew.app import settle as settle_cmd

    settings = _settings(tmp_path)
    _script_models(monkeypatch, backtest_cmd)
    _script_models(monkeypatch, settle_cmd)
    runs_root, settle_root = tmp_path / "bt", tmp_path / "settle"
    code = backtest_cmd.run(settings, ("ACME",), GRID[0], GRID[-1], 5, "t1", False,
                            runs_root=runs_root, settle_root=settle_root)
    assert code == 0
    batch = runs_root / "t1"
    lines = (batch / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert [json.loads(line)["as_of"] for line in lines] == list(GRID)
    cells = json.loads((batch / "progress.json").read_text(encoding="utf-8"))["cells"]
    assert set(cells) == {f"ACME@{day}" for day in GRID}
    assert all(cell["status"] == "delivered" for cell in cells.values())

    # 先结算后分析（不变量 20）：每个格子的课程只含当日之前可知的教训
    boards = {day: _board(batch, cells[f"ACME@{day}"]["run"]) for day in GRID}
    assert boards[GRID[0]]["memory/lessons"] == []                        # 第一天什么都不知道
    assert [e["as_of"] for e in boards[GRID[1]]["memory/lessons"]] == [GRID[0]]
    assert [e["as_of"] for e in boards[GRID[2]]["memory/lessons"]] == [GRID[1], GRID[0]]

    # 结算把走完窗口的决策落了账（持有期 3：前两笔可知，最后一份还 pending），教训已写
    records = DecisionLog(tmp_path / "decisions.jsonl").records()
    outcomes = [r for r in records if isinstance(r, OutcomeRecord)]
    assert {o.as_of for o in outcomes} == {GRID[0], GRID[1]}
    assert all(o.lesson for o in outcomes)
    assert len([r for r in records if isinstance(r, DecisionRecord)]) == 3

    # 同 --run-id 续跑：不做任何新事
    before = sorted(p.name for p in batch.iterdir())
    code = backtest_cmd.run(settings, ("ACME",), GRID[0], GRID[-1], 5, "t1", False,
                            runs_root=runs_root, settle_root=settle_root)
    assert code == 0
    assert (batch / "decisions.jsonl").read_text(encoding="utf-8").splitlines() == lines
    assert sorted(p.name for p in batch.iterdir()) == before
    assert "已完成 3 个" in capsys.readouterr().out


def test_backtest_resumes_after_the_budget_stops_it(tmp_path, monkeypatch, capsys):
    tight = _settings(tmp_path, budget={"batch_tokens": 15000})           # 两次交付后超限
    _script_models(monkeypatch, backtest_cmd)
    runs_root, settle_root = tmp_path / "bt", tmp_path / "settle"
    code = backtest_cmd.run(tight, ("ACME",), GRID[0], GRID[-1], 5, "t2", False,
                            runs_root=runs_root, settle_root=settle_root)
    assert code == 0
    batch = runs_root / "t2"
    assert len((batch / "decisions.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert "批次预算超限" in capsys.readouterr().out

    code = backtest_cmd.run(_settings(tmp_path), ("ACME",), GRID[0], GRID[-1], 5, "t2", False,
                            runs_root=runs_root, settle_root=settle_root)   # 加预算续跑
    assert code == 0
    lines = (batch / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["as_of"] for line in lines] == list(GRID)   # 只补了第三个格子


def test_backtest_refuses_a_real_model_without_yes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(backtest_cmd, "describe_models",
                        lambda settings: ("model", {"quick": "m", "deep": "m"}, ""))
    code = backtest_cmd.run(_settings(tmp_path), ("ACME",), GRID[0], GRID[-1], 5, "t3", False,
                            runs_root=tmp_path / "bt", settle_root=tmp_path / "settle")
    assert code == 2
    assert "--yes" in capsys.readouterr().out
    assert not (tmp_path / "bt").exists()                                # 拒绝时什么都不建


@pytest.mark.parametrize("from_date,to_date", [
    ("2026-13-01", "2026-08-17"),          # 日期无法解析
    ("2026-08-17", "2026-08-03"),          # 范围颠倒
])
def test_backtest_rejects_a_bad_range(tmp_path, from_date, to_date, capsys):
    code = backtest_cmd.run(_settings(tmp_path), ("ACME",), from_date, to_date, 5, "t4", False,
                            runs_root=tmp_path / "bt", settle_root=tmp_path / "settle")
    assert code == 2
    assert capsys.readouterr().out


def test_backtest_rejects_a_ticker_outside_the_universe(tmp_path, capsys):
    code = backtest_cmd.run(_settings(tmp_path), ("NOPE",), GRID[0], GRID[-1], 5, "t5", False,
                            runs_root=tmp_path / "bt", settle_root=tmp_path / "settle")
    assert code == 2
    assert "NOPE" in capsys.readouterr().out


def test_settle_command_resolves_and_prints_per_outcome_lines(tmp_path, monkeypatch, capsys):
    from insightcrew.app import settle as settle_cmd

    settings = _settings(tmp_path)
    _script_models(monkeypatch, settle_cmd)
    DecisionLog(tmp_path / "decisions.jsonl").append(_seed())
    code = settle_cmd.run(settings, GRID[-1], settle_root=tmp_path / "settle")
    assert code == 0
    out = capsys.readouterr().out
    assert "新落账 1 条结果" in out and "收益" in out and "教训已落账" in out
    assert DecisionLog(tmp_path / "decisions.jsonl").pending() == []
