"""代码的规范化与校验。用户输入的任何文本先经过 normalize，才会成为系统里的一个代码。

规则来自 BLUEPRINT 6.2：只接受港股（1 到 5 位数字 .HK）与美股（1 到 6 位字母数字）。
指数代码（^ 开头）只作为基准使用，一律不接受用户输入。
"""
from __future__ import annotations

import re

from .market import InvalidTicker

_HK = re.compile(r"^(\d{1,5})\.HK$")
_US = re.compile(r"^[A-Z][A-Z0-9]{0,5}$")


def normalize(text: str) -> str:
    """把用户输入规范化为标准代码；不是本系统接受的代码就抛 InvalidTicker。

    先去首尾空白再转大写。港股数字补齐到四位（五位且以 0 开头的去掉这个 0，
    其余五位代码原样保留）；美股原样通过；其余一律拒绝，包括指数代码。
    """
    ticker = text.strip().upper()
    if _HK.match(ticker):
        number = ticker[:-3]
        if len(number) == 5:
            number = number.removeprefix("0")   # 五位且以 0 开头：去掉这个 0，得四位
        return f"{number.zfill(4)}.HK"
    if _US.match(ticker):
        return ticker
    raise InvalidTicker(f"不是本系统接受的代码：{text!r}")


def file_stem(ticker: str) -> str:
    """规范化代码对应的 CSV 文件名（不含扩展名）。指数的 ^ 换成下划线。

    只对 normalize 产出的代码调用，用户文本永远到不了路径里。
    """
    return ticker.replace("^", "_")


def benchmark_for(ticker: str) -> str:
    """该代码对应的指数基准：港股是恒生指数，其余是标普 500。抓真实数据时使用。"""
    return "^HSI" if ticker.endswith(".HK") else "SPY"
