"""运行页面：把一份事件日志嵌进单文件页面，写进运行目录。

页面是唯一的前端资产（app/assets/page.html），静态与实时共用同一份文件；
这里的职责只有一件事：把事件序列（和可选的价格序列）安全地嵌进 asset 的 #embedded 元素。
嵌入的 JSON 里每个 "<" 都写成 \\u003c，模型回复里的任何文本都无法提前结束这个元素。
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from ..kernel.events import Event

_ASSET = Path(__file__).resolve().parent / "assets" / "page.html"
_SLOT = '<script type="application/json" id="embedded">null</script>'


def render(events: Iterable[Event], bars: list[dict[str, object]] | None = None) -> str:
    """静态模式：整段事件（与可选的收盘价）嵌进页面。页面从此不再发起任何网络请求。"""
    payload = json.dumps({"events": [e.model_dump(mode="json") for e in events],
                          "bars": bars if bars is not None else None},
                         ensure_ascii=False).replace("<", "\\u003c")
    asset = _ASSET.read_text(encoding="utf-8")
    if _SLOT not in asset:
        raise RuntimeError("页面资产缺少嵌入槽位；请勿手改 #embedded 元素")
    return asset.replace(_SLOT, f'<script type="application/json" id="embedded">{payload}</script>')


def write(run_dir: Path, events: Iterable[Event], bars: list[dict[str, object]] | None = None) -> Path:
    """把静态回放页写进运行目录（replay.html）。"""
    path = run_dir / "replay.html"
    path.write_text(render(events, bars), encoding="utf-8")
    return path


def live_page() -> str:
    """实时模式：页面内嵌槽位保持 null，事件从本地服务器的事件流读取。"""
    return _ASSET.read_text(encoding="utf-8")
