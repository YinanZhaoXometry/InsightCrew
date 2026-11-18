"""replay 命令：从一份事件日志无模型重放一次运行。WP5 留空，WP6 填写。

签名是约定：cli.py 已经按它调用；填写时保持签名，不改 cli.py。
"""
from __future__ import annotations

from .settings import Settings


def run(settings: Settings, run_dir: str) -> int:
    """按 run.started 里的请求与设置重放，比较结果与黑板快照是否一致。"""
    print("此命令尚未实现")
    return 2
