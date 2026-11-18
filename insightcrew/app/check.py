"""check 命令：对配置的每个档位逐一探测模型。WP5 留空，WP6 填写。

签名是约定：cli.py 已经按它调用；填写时保持签名，不改 cli.py。
"""
from __future__ import annotations

from .settings import Settings


def run(settings: Settings) -> int:
    """打印配置（不含密钥），并对每个档位在四种输出方式下各发一次小请求。"""
    print("此命令尚未实现")
    return 2
