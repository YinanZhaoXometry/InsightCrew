"""页面的 Node 侧测试（BLUEPRINT 20.12）：跑 tests/js/ 下的 .test.mjs（node --test），
并对拼装后的整段脚本做一次语法检查（node --check）。
Node 只是开发机的便利，不是依赖：机器上没有 node 时整个文件跳过并说明原因。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from insightcrew.app.replay_html import live_page

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="这台机器上没有 node；页面的 Node 测试跳过（BLUEPRINT 20.12）")


def _assembled_script() -> str:
    """拼装后页面里那一个真正的脚本（第二个 script 元素）的全文。"""
    page = live_page()
    start = page.index("<script>\n") + len("<script>\n")
    end = page.rindex("</script>")
    return page[start:end]


def test_the_assembled_script_passes_node_check(tmp_path: Path) -> None:
    assert NODE is not None                       # 否则整个文件已被跳过
    path = tmp_path / "assembled.js"
    path.write_text(_assembled_script(), encoding="utf-8")
    result = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_page_node_tests_pass() -> None:
    assert NODE is not None                       # 否则整个文件已被跳过
    tests = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "tests" / "js").glob("*.test.mjs"))
    assert tests, "tests/js/ 下没有 .test.mjs"
    result = subprocess.run([NODE, "--test", *tests], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
