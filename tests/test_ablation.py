"""消融评测的机制：四种配置、从日志读出的每格结果、汇总。用场景的脚本化回复代替真实模型。"""
from __future__ import annotations

import asyncio
import json
from functools import partial
from pathlib import Path

from insightcrew.app.settings import Settings, load
from insightcrew.app.wiring import ROOT, open_run
from insightcrew.evals import ablation
from insightcrew.evals.run import run as eval_run
from insightcrew.evals.scenarios import load_one
from insightcrew.flows.analyze import FlowConfig, analyze

SETTINGS: Settings = load(ROOT, force_offline=True)
CELL = (("ACME", "2026-09-30"),)


def _cells(scenario: str, tmp_path: Path) -> dict[str, ablation.Cell]:
    opener = partial(open_run, SETTINGS, scenario=load_one(scenario), runs_dir=tmp_path)
    cells = asyncio.run(ablation.run_cells(opener, analyze, FlowConfig(), CELL))
    return {c.config: c for c in cells}


def test_the_four_configurations_differ_only_in_what_is_ablated() -> None:
    cfgs = ablation.configs(FlowConfig(audit_rounds=2))
    assert list(cfgs) == ["full", "no_judges", "shadow", "bare"]
    assert (cfgs["no_judges"].judges, cfgs["no_judges"].audit) == (False, "on")
    assert (cfgs["shadow"].audit, cfgs["shadow"].debate_rounds) == ("shadow", 1)
    assert (cfgs["bare"].audit, cfgs["bare"].judges, cfgs["bare"].debate_rounds) == ("shadow", False, 0)
    assert all(cfg.audit_rounds == 2 for cfg in cfgs.values())


def test_a_clean_run_is_delivered_in_every_configuration_with_fewer_calls_as_pieces_go(tmp_path: Path) -> None:
    cells = _cells("clean", tmp_path)
    assert all(c.status == "delivered" and not c.would_reject for c in cells.values())
    assert cells["full"].calls > cells["no_judges"].calls == cells["shadow"].calls > cells["bare"].calls
    assert cells["full"].rating is not None
    assert all(c.tokens == c.calls * 1300 and c.retries == 0 for c in cells.values())   # Script 的固定用量


def test_a_shadow_audit_delivers_what_the_audit_would_have_rejected(tmp_path: Path) -> None:
    cells = _cells("demo", tmp_path)                    # 交易员第一版违反一条规则
    assert cells["shadow"].status == "delivered" and cells["shadow"].would_reject
    assert cells["shadow"].revisions == 0
    assert not cells["no_judges"].would_reject and cells["no_judges"].revisions == 1


def test_summary_and_archive(tmp_path: Path) -> None:
    cells = list(_cells("demo", tmp_path / "runs").values())
    rows = {r["config"]: r for r in ablation.summarise(cells)}
    assert rows["shadow"]["would_reject"] == 1 and rows["no_judges"]["would_reject"] == 0
    assert rows["full"]["cost_per_run"] is None                 # 脚本化模型没有价格
    assert "| shadow | 1 | 1 | 1/1 |" in ablation.table(list(rows.values()))


def test_ablation_without_a_model_refuses(capsys) -> None:
    assert eval_run(ROOT, "ablation", no_model_reason="没有密钥") == 2
    assert "没有密钥" in capsys.readouterr().out


def test_main_writes_the_archive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ablation, "CELLS", CELL)
    opener = partial(open_run, SETTINGS, scenario=load_one("clean"), runs_dir=tmp_path / "runs")
    assert asyncio.run(ablation.main(opener, analyze, FlowConfig(), archive=tmp_path)) == 0
    data = json.loads((tmp_path / "ablation.json").read_text(encoding="utf-8"))
    assert len(data["cells"]) == 4 and {r["config"] for r in data["summary"]} == set(ablation.configs(FlowConfig()))
