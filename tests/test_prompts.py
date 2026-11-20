"""提示词完整性测试（BLUEPRINT 20.12）：提示词与加载它的角色必须一致。

四项检查，逐角色对照 ``roles/__init__.py`` 的声明：

1. ``roles/prompts/`` 里的每个文件都至少被一个角色加载；
2. 任何角色的提示词里都看不到 ``<!--`` 版权行（``roles._prompt`` 负责丢弃）；
3. 提示词在反引号里点名的黑板键，必须是该角色 reads 或 writes 里的键；
4. 读 ``*/status`` 键的角色，提示词必须解释 ``unavailable`` 是什么意思。

只有今天全部通过的检查才提交（20.12）；一条不通过的检查是 WP38 的发现，记在
HANDOFF 第 7 节，不进这个文件——只有 WP38 之后会改提示词。
"""
from __future__ import annotations

import re
from pathlib import Path

from insightcrew import roles
from insightcrew.kernel.runtime import Role

_PROMPTS = Path(roles.__file__).parent / "prompts"
# TECHNICAL_TOOLS 与 TECHNICAL 是同一角色的开工具形态（同键同提示词），一并检查。
ROLES: tuple[Role, ...] = (*roles.ALL, roles.TECHNICAL_TOOLS)
# 改编声明的可见部分（BLUEPRINT 19.4）：整行以 <!-- 开头，正常情况下模型看不到。
_ATTRIBUTION = "Adapted from TradingAgents"
# 黑板键的形状：actor/键名，actor 可带点号（judge.evidence/verdict），键名可带斜杠。
_KEY_SHAPE = re.compile(r"[a-z][a-z0-9_.]*/[\w/*]+", re.ASCII)


def _visible_lines(name: str) -> list[str]:
    """模型可见的行：照抄 ``roles._prompt`` 的规则，丢弃以 ``<!--`` 开头的版权行。"""
    text = (_PROMPTS / name).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if not line.startswith("<!--")]


def test_every_prompt_file_is_loaded_by_at_least_one_role() -> None:
    """检查 1：prompts/ 目录里没有孤立的提示词文件。"""
    files = sorted(_PROMPTS.glob("*.md"))
    assert files, "roles/prompts/ 里没有提示词文件"
    for path in files:
        # 含 {占位符} 的行（judge.md 的 {lens}）按角色不同被填成不同文字，跳过；
        # 其余每一行都必须原样出现在某个角色的提示词里，且必须是同一个角色。
        lines = [line for line in _visible_lines(path.name)
                 if line.strip() and "{" not in line and "}" not in line]
        assert lines, f"{path.name} 里没有可核对的行"
        assert any(all(line in role.instructions for line in lines) for role in ROLES), (
            f"{path.name} 没有被任何角色加载")


def test_no_role_sees_the_attribution_line() -> None:
    """检查 2：版权行只属于文件，不属于模型看到的提示词。"""
    for role in ROLES:
        assert "<!--" not in role.instructions, f"{role.name} 的提示词里有 <!--"
        assert _ATTRIBUTION not in role.instructions, f"{role.name} 看得到改编声明行"


def test_backticked_board_keys_are_declared_on_the_role() -> None:
    """检查 3：反引号里点名的黑板键必须是该角色 reads 或 writes 里的键。"""
    for role in ROLES:
        allowed = set(role.reads) | {role.writes}
        for token in re.findall(r"`([^`\n]+)`", role.instructions):
            if _KEY_SHAPE.fullmatch(token):
                assert token in allowed, (
                    f"{role.name} 的提示词点名了 {token}，但它既不在 reads 里，也不是 writes")


def test_status_readers_explain_what_unavailable_means() -> None:
    """检查 4：读 */status 键的角色必须解释 unavailable，不许把缺席当默认。"""
    for role in ROLES:
        if any(key.endswith("/status") for key in role.reads):
            assert "unavailable" in role.instructions, (
                f"{role.name} 读 */status 键，但提示词没有解释 unavailable 是什么意思")
