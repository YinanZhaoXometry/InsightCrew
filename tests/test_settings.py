"""设置模块的测试：三种来源的优先级、未知键被点名、密钥不出现在打印结果里。"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from insightcrew.app.settings import load

TOML = """\
[run]
universe = "synthetic"
output_mode = "auto"

[flow]
debate_rounds = 1
judges = true
audit_rounds = 3
judge_rounds = 2
quorum = 2

[limits]
max_requests = 40
max_seconds = 600
request_timeout_s = 120

[budget]
run_tokens = 200000
run_cost = 0.0
batch_tokens = 0
batch_cost = 0.0

[tiers]
quick = "deepseek:deepseek-chat"
deep = "deepseek:deepseek-chat"
"""


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """把本进程里已有的 IC_*、DEEPSEEK* 变量清走，并在测试后恢复原样（load_dotenv 会写入进程环境）。"""
    names = [k for k in os.environ if k.startswith(("IC_", "DEEPSEEK"))]
    before = {k: os.environ[k] for k in names}
    for k in names:
        monkeypatch.delenv(k, raising=False)
    yield
    for k in [k for k in os.environ if k.startswith(("IC_", "DEEPSEEK"))]:
        if k not in before:
            del os.environ[k]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "insightcrew.toml").write_text(TOML, encoding="utf-8")
    return tmp_path


def test_file_is_the_base(root: Path) -> None:
    s = load(root)
    assert s.run.universe == "synthetic"
    assert s.flow.debate_rounds == 1
    assert s.limits.max_requests == 40
    assert s.budget.run_tokens == 200000
    assert s.tiers.quick == "deepseek:deepseek-chat"


def test_environment_overrides_file(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IC_RUN__UNIVERSE", "real")
    assert load(root).run.universe == "real"


def test_flag_overrides_environment(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IC_RUN__UNIVERSE", "real")
    s = load(root, run={"universe": "demo"})
    assert s.run.universe == "demo"


def test_env_override_touches_only_one_key(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """覆盖分节里的一个键，不得丢掉该分节里文件给的其余键。"""
    monkeypatch.setenv("IC_FLOW__JUDGES", "false")
    s = load(root)
    assert s.flow.judges is False
    assert s.flow.debate_rounds == 1
    assert s.flow.quorum == 2


def test_dotenv_fills_only_unset_variables(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (root / ".env").write_text("IC_BUDGET__RUN_TOKENS=12345\nDEEPSEEK_API_KEY=sk-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("IC_BUDGET__RUN_TOKENS", "999")
    s = load(root)
    assert s.budget.run_tokens == 999          # 进程环境优先于 .env
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-dotenv"   # 供应商密钥进入进程环境，库从这里读


def test_dotenv_alone_provides_the_value(root: Path) -> None:
    (root / ".env").write_text("IC_TIERS__DEEP=scripted:deep\n", encoding="utf-8")
    assert load(root).tiers.deep == "scripted:deep"


def test_unknown_key_in_file_is_refused(root: Path) -> None:
    bad = TOML.replace('output_mode = "auto"', 'output_mode = "auto"\nno_such_key = 1')
    (root / "insightcrew.toml").write_text(bad, encoding="utf-8")
    with pytest.raises(Exception, match="no_such_key"):
        load(root)


def test_unknown_section_in_file_is_refused(root: Path) -> None:
    (root / "insightcrew.toml").write_text(TOML + "\n[what]\nx = 1\n", encoding="utf-8")
    with pytest.raises(Exception, match="what"):
        load(root)


def test_unknown_env_key_is_refused(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IC_RUN__TYPO", "1")
    with pytest.raises(Exception, match=r"IC_RUN__TYPO|typo"):
        load(root)


def test_unknown_env_variable_is_refused(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IC_MODEL", "leftover-from-v1")
    with pytest.raises(Exception, match="IC_MODEL"):
        load(root)


def test_api_key_never_appears_in_printed_settings(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IC_API_KEY", "sk-secret-123")
    s = load(root)
    assert "sk-secret-123" not in repr(s)
    assert "sk-secret-123" not in str(s)
    assert "**********" in repr(s)


def test_today_and_force_offline_from_environment(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IC_TODAY", "2027-01-01")
    monkeypatch.setenv("IC_FORCE_OFFLINE", "true")
    s = load(root)
    assert s.today == date(2027, 1, 1)
    assert s.force_offline is True


def test_flag_can_pin_today(root: Path) -> None:
    assert load(root, today=date(2028, 3, 4)).today == date(2028, 3, 4)


# ---- 证据通道的设置（BLUEPRINT 18.11）：[disclosures] 分节与 IC_SEC_CONTACT 根级变量 ----


def test_disclosures_defaults_when_the_section_is_absent(root: Path) -> None:
    s = load(root)                                    # fixture 的 TOML 不写 [disclosures]
    assert s.disclosures.window_days == 90
    assert s.disclosures.max_items == 30


def test_disclosures_section_is_read_from_the_file(root: Path) -> None:
    (root / "insightcrew.toml").write_text(
        TOML + "\n[disclosures]\nwindow_days = 120\nmax_items = 10\n", encoding="utf-8")
    s = load(root)
    assert (s.disclosures.window_days, s.disclosures.max_items) == (120, 10)


def test_disclosures_env_overrides_one_key(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IC_DISCLOSURES__WINDOW_DAYS", "60")
    s = load(root)
    assert s.disclosures.window_days == 60 and s.disclosures.max_items == 30


def test_unknown_key_in_disclosures_is_refused(root: Path) -> None:
    (root / "insightcrew.toml").write_text(
        TOML + "\n[disclosures]\nwindow_dayz = 60\n", encoding="utf-8")
    with pytest.raises(Exception, match="window_dayz"):
        load(root)


def test_sec_contact_from_environment_is_masked(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IC_SEC_CONTACT", "Zhang San zsan@example.com")
    s = load(root)
    assert s.sec_contact is not None
    assert s.sec_contact.get_secret_value() == "Zhang San zsan@example.com"
    assert "zsan@example.com" not in repr(s) and "zsan@example.com" not in str(s)


def test_sec_contact_absent_is_none(root: Path) -> None:
    assert load(root).sec_contact is None
