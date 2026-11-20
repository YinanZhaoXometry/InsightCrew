"""运行页面：把一份事件日志嵌进单文件页面，写进运行目录。

页面资产拆成三处（BLUEPRINT 20.9）：assets/page.html 是外壳（头部、CSP、静态标记、
两处标记行与 #embedded 槽位），assets/css/ 与 assets/js/ 是按文件名顺序拼装的源文件。
这里的职责只有两件事：把 css/ 与 js/ 的文件按文件名顺序拼进两处标记行（输出确定），
再把事件序列（和可选的价格序列、耗时轨迹）安全地嵌进 asset 的 #embedded 元素。
嵌入的 JSON 里每个 "<" 都写成 \\u003c，模型回复里的任何文本都无法提前结束这个元素。
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from ..kernel.events import Event

_ASSETS = Path(__file__).resolve().parent / "assets"
_PAGE = _ASSETS / "page.html"
_SLOT = '<script type="application/json" id="embedded">null</script>'
_CSS_MARKER = "/*@@css*/"
_JS_MARKER = "/*@@js*/"


def _joined(folder: str, suffix: str) -> str:
    """一个资产目录里的全部源文件，按文件名顺序拼成一段文本（拼装是确定性的）。"""
    files = sorted(path for path in (_ASSETS / folder).iterdir() if path.suffix == suffix)
    if not files:
        raise RuntimeError(f"assets/{folder}/ 里没有源文件，页面拼不起来")
    return "\n\n".join(path.read_text(encoding="utf-8") for path in files)


def _assemble() -> str:
    """外壳 + css/ + js/：两处标记行各出现一次，替换为按文件名排序的源文件内容。"""
    page = _PAGE.read_text(encoding="utf-8")
    for marker in (_CSS_MARKER, _JS_MARKER):
        if page.count(marker) != 1:
            raise RuntimeError(f"页面资产缺少或重复出现标记行 {marker}；请勿手改 page.html 的拼装结构")
    if _SLOT not in page:
        raise RuntimeError("页面资产缺少嵌入槽位；请勿手改 #embedded 元素")
    css, js = _joined("css", ".css"), _joined("js", ".js")
    for text in (css, js):                     # 任何源文件里的 </script> 都会提前结束页面脚本
        if "</script" in text:
            raise RuntimeError("assets/ 里的源文件含有 </script>，会提前结束页面脚本")
    return page.replace(_CSS_MARKER, css).replace(_JS_MARKER, js)


def render(events: Iterable[Event], bars: list[dict[str, object]] | None = None,
           trace: list[dict[str, object]] | None = None) -> str:
    """静态模式：整段事件（与可选的收盘价、耗时轨迹）嵌进页面。页面从此不再发起任何网络请求。"""
    payload = json.dumps({"events": [e.model_dump(mode="json") for e in events],
                          "bars": bars if bars is not None else None,
                          "trace": trace if trace is not None else None},
                         ensure_ascii=False).replace("<", "\\u003c")
    return _assemble().replace(_SLOT, f'<script type="application/json" id="embedded">{payload}</script>')


def write(run_dir: Path, events: Iterable[Event], bars: list[dict[str, object]] | None = None,
          trace: list[dict[str, object]] | None = None) -> Path:
    """把静态回放页写进运行目录（replay.html）。"""
    path = run_dir / "replay.html"
    path.write_text(render(events, bars, trace), encoding="utf-8")
    return path


def live_page() -> str:
    """实时模式：拼装后的页面，嵌入槽位保持 null，事件从本地服务器的事件流读取。"""
    return _assemble()
