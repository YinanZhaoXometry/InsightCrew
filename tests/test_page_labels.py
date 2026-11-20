"""标签完整性（BLUEPRINT 20.12）：规则手册里的每条规则、领域层的每个审计检查名、
流程层的每个停机原因，在拼装后的页面里都要有自己的标签；缺失的标签会让页面落到生键名。"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from insightcrew.app.replay_html import live_page

ROOT = Path(__file__).resolve().parent.parent
PAGE = live_page()


def test_every_rule_id_has_a_label() -> None:
    with (ROOT / "data" / "rules.toml").open("rb") as f:
        rules = tomllib.load(f)
    assert rules
    for rule_id in rules:
        assert rule_id in PAGE, f"规则 {rule_id} 在页面里没有标签"


def test_every_audit_check_has_a_label() -> None:
    checks: set[str] = set()
    for path in sorted((ROOT / "insightcrew" / "domain").glob("*.py")):
        checks.update(re.findall(r'check="([a-z0-9_]+)"', path.read_text(encoding="utf-8")))
    assert checks
    for check in checks:
        assert check in PAGE, f"审计检查 {check} 在页面里没有标签"


def test_every_halt_reason_has_a_label() -> None:
    reasons: set[str] = set()
    for path in sorted((ROOT / "insightcrew" / "flows").glob("*.py")):
        reasons.update(re.findall(r'Halt\("([a-z_]+)"', path.read_text(encoding="utf-8")))
    assert reasons
    for reason in reasons:
        assert reason in PAGE, f"停机原因 {reason} 在页面里没有标签"
