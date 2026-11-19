"""社交情感的证据阶段（BLUEPRINT 19.12）。WP34 的正文。

与 stage_news 的新闻部分同一套读法：窗口 [as_of − deps.sentiment_window_days, as_of]
两端含（19.8 第 2 条），帖子按发布日新到旧排序后截到 deps.sentiment_max_posts，
编号 S1 起（S1 最新）；无日期的帖子由端口丢弃并计数，正文截到 280 字也是端口的
职责。读不到绝不停下运行（决策 35）：不可用就写明原因，继续。能力关 = 一个键
都不写（19.8 第 4 条）。
"""
from __future__ import annotations

from datetime import date, timedelta

from ..domain.market import NotCovered, SourceUnavailable
from ..domain.social import PostEntry, SocialCard
from ..domain.types import ChannelStatus, Scope
from ..flows.deps import Deps
from ..kernel.runtime import Run


def evidence(run: Run, deps: Deps, channels: tuple[str, ...]) -> None:
    """读社交窗口，写 sentiment/posts、sentiment/card、sentiment/status。

    channels 由 stages.evidence 传入：本阶段只在 "sentiment" 被点名时才会被调用，
    参数保持 WP31 留桩时的最终签名。
    """
    scope = run.board.get("request/scope", Scope)
    as_of = date.fromisoformat(scope.as_of)
    if deps.social is None:
        _off(run, "尚未接入社交数据源")
        return
    start = as_of - timedelta(days=deps.sentiment_window_days)
    try:
        fetched = deps.social.posts(scope.ticker, start, as_of)
    except NotCovered as e:
        _off(run, f"该窗口未覆盖：{e}")
        return
    except SourceUnavailable as e:
        _off(run, str(e))
        return
    ordered = sorted(fetched.posts, key=lambda p: (-p.published.toordinal(), p.url))
    kept = ordered[:deps.sentiment_max_posts]
    posts = [PostEntry(id=f"S{i}", published=p.published.isoformat(), source=p.source,
                       text=p.text, tag=p.tag)
             for i, p in enumerate(kept, start=1)]
    run.board.put("sentiment/posts", posts, by="evidence")
    run.board.put("sentiment/card", SocialCard(
        source=fetched.source, window_from=start.isoformat(), window_to=as_of.isoformat(),
        items=len(posts), dropped=fetched.dropped,
        truncated=max(0, len(ordered) - len(kept))), by="evidence")
    run.board.put("sentiment/status",
                  ChannelStatus(channel="sentiment", state="available" if posts else "empty",
                                reason="" if posts else
                                f"窗口 {start.isoformat()} 到 {as_of.isoformat()} 内没有帖子"),
                  by="evidence")


def _off(run: Run, reason: str) -> None:
    """通道不可用：只写状态与原因，此外一个键都不写（19.8 第 4 条）。"""
    run.board.put("sentiment/status",
                  ChannelStatus(channel="sentiment", state="unavailable", reason=reason), by="evidence")
