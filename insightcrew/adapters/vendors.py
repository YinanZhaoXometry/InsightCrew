# Adapted from TradingAgents (Apache-2.0), modified.
"""数据源链与错误脱敏（BLUEPRINT 19.12、19.8）：多源按序尝试，错误按类型决定去留。

first_ok(chain) 依次调用每个源的零参闭包：NoData / NotCovered（这一源没有数据、或覆盖不到
这个标的与窗口）、SourceUnavailable（这一源读不出来，与标的无关）与 NotConfigured（缺密钥）
都换下一个源，每家失败的原因都记下来；全部失败时抛 SourceUnavailable，消息逐家列出名字与
原因，先过 scrub。其它异常（如 ValueError）原样抛出：那不是"这一源不行"，是代码或配置错
了，链不该吞掉它（拒绝而不是猜）。

scrub(text) 抹掉文本里可能带密钥的内容：URL 查询参数里名字像 key / token / api_key 的
整对删除；任何名字以 KEY 或 TOKEN 结尾的环境变量的值（长度至少 4，太短的不替换，免得
误伤普通文本）一律换成 ***。适配器抛出的消息里凡带 URL 的都先过 scrub 再抛（陷阱清单：
绝不打印或记录带密钥的 URL）。

改编自 TradingAgents 的 dataflows/router.py（按序回退与错误按"调用方的反应"归类）、
errors.py（错误分类的思想；这里直接用 domain/market.py 已有的类型）与 net.py
（密钥不得进入任何错误消息）。
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Sequence
from urllib.parse import urlsplit, urlunsplit

from ..domain.market import NoData, NotConfigured, NotCovered, SourceUnavailable

# URL 的字符集（RFC 3986 的保留与未保留字符）：全角括号与中文不在其中，正则自然停在 URL 尾。
_URL = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
_PARAM_SPLIT = re.compile(r"[_\-]")
_SECRET_TAILS = ("key", "token")            # 参数名按 - 或 _ 分段后的最后一段是它，就当密钥名
_SECRET_NAMES = {"apikey", "api_token"}     # 不带分隔符的常见写法
_ENV_SUFFIXES = ("KEY", "TOKEN")            # 环境变量名以它结尾，值就当密钥
_MASK = "***"
_MIN_VALUE = 4                              # 太短的值不按值替换：环境变量里可能存着普通短词


def first_ok[T](chain: Sequence[tuple[str, Callable[[], T]]]) -> tuple[str, T]:
    """按序试每个数据源，返回第一家成功者的（名字, 结果）。

    一家抛 NoData / NotCovered / SourceUnavailable / NotConfigured 都换下一家；全部失败时
    抛 SourceUnavailable，消息逐家列出名字与原因（先过 scrub，消息里可能带着 URL）。
    其它异常原样抛出：链只消化"这一源不行"，不消化代码错误。
    """
    reasons: list[str] = []
    for name, call in chain:
        try:
            return name, call()
        except NoData as e:
            reasons.append(f"{name}（没有数据：{e}）")
        except NotCovered as e:
            reasons.append(f"{name}（没有覆盖：{e}）")
        except SourceUnavailable as e:
            reasons.append(f"{name}（读不出来：{e}）")
        except NotConfigured as e:
            reasons.append(f"{name}（未配置：{e}）")
    detail = "；".join(reasons) if reasons else "链上没有任何数据源"
    raise SourceUnavailable(scrub(f"数据源链全部失败：{detail}"))


def scrub(text: str) -> str:
    """抹掉文本里可能带密钥的内容：先按值替换（名字以 KEY/TOKEN 结尾的环境变量），再删 URL 里的密钥参数。"""
    for name, value in os.environ.items():
        if name.endswith(_ENV_SUFFIXES) and len(value) >= _MIN_VALUE:
            text = text.replace(value, _MASK)
    return _URL.sub(_scrub_url, text)


# ---- 内部 ----


def _scrub_url(match: re.Match[str]) -> str:
    """把一条 URL 查询串里名字像密钥的参数整对删掉，其余参数原样保留。"""
    url = match.group(0)
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [piece for piece in parts.query.split("&") if not _is_secret_param(piece.partition("=")[0])]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), parts.fragment))


def _is_secret_param(name: str) -> bool:
    """参数名是否像密钥：apikey / api_token 直通；按 - 或 _ 分段后最后一段是 key 或 token。"""
    lowered = name.lower()
    if lowered in _SECRET_NAMES:
        return True
    return _PARAM_SPLIT.split(lowered)[-1] in _SECRET_TAILS
