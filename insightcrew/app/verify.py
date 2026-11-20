"""verify 命令：一份运行日志的离线结构核对（BLUEPRINT 20.7 第 12 条）。

不调模型、不跑流程、不读 trace：只读 journal.jsonl 本身，核对它的结构是否可能是
一次诚实的运行。八条检查：

    1. 序号从 1 起、无断档；
    2. 第一条事件是 run.started；
    3. run.finished 至多一条，且是最后一条；
    4. 每一个 caused_by 都指向一条更早的事件；
    5. 每个键的产物版本逐一加一；
    6. 每次输出非空的 model.called 之后都有以其为因的 artifact.written；
    7. run/usage（在时）等于 model.called 的合计；
    8. run/outcome 恰好在 run.finished 在时在。

好的日志打印"通过"退出 0；没有 run.finished 的日志也通过，附注"未结束"；
其余逐条打印问题，退出 1。行本身解析不过是问题（残行不截，那是 resume 的事）。
"""
from __future__ import annotations

import json
from typing import Any

from .replay import _journal_path, _one_line


def run(run_dir: str) -> int:
    """核对 RUN_DIR 里的日志。打印 通过 或问题清单；退出 0 或 1。"""
    path = _journal_path(run_dir)
    if path is None:
        print(f"找不到运行日志：{run_dir}（应是一个包含 journal.jsonl 的运行目录）")
        return 1
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"无法读取运行日志 {path}：{_one_line(str(e))}")
        return 1
    events, problems = _parse(text)
    problems.extend(_structure(events))
    unfinished = not any(event.get("type") == "run.finished" for event in events)
    if problems:
        print("未通过：" if not unfinished else "未通过（日志未结束）：")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("通过（未结束）" if unfinished else "通过")
    return 0


def _parse(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """逐行读 JSON；解析不过的行是问题，不中断其余检查。"""
    events: list[dict[str, Any]] = []
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except ValueError:
            problems.append(f"第 {number} 行不是合法 JSON")
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
        else:
            problems.append(f"第 {number} 行不是 JSON 对象")
    return events, problems


def _structure(events: list[dict[str, Any]]) -> list[str]:
    """八条结构检查；返回的问题清单为空即通过。"""
    problems: list[str] = []
    seqs = {event.get("seq") for event in events}
    for index, event in enumerate(events, 1):
        if event.get("seq") != index:
            problems.append(f"序号断档：第 {index} 条事件的序号是 {event.get('seq')!r}")
            break
    if not events:
        problems.append("日志里没有事件")
    elif events[0].get("type") != "run.started":
        problems.append(f"第一条事件是 {events[0].get('type')!r}，应为 run.started")
    finished = [index for index, event in enumerate(events) if event.get("type") == "run.finished"]
    if len(finished) > 1:
        problems.append(f"run.finished 出现 {len(finished)} 次，至多一次")
    elif finished and finished[0] != len(events) - 1:
        problems.append("run.finished 不是最后一条事件")
    for event in events:
        for cause in event.get("caused_by") or ():
            if not isinstance(cause, int) or cause not in seqs:
                problems.append(f"序号 {event.get('seq')} 的 caused_by 指向不存在的事件 {cause}")
            elif isinstance(event.get("seq"), int) and cause >= event["seq"]:
                problems.append(f"序号 {event.get('seq')} 的 caused_by 指向不更早的事件 {cause}")
    versions: dict[str, int] = {}
    for event in events:
        if event.get("type") != "artifact.written":
            continue
        data = event.get("data") or {}
        key = data.get("key", "<无名>")
        versions[key] = versions.get(key, 0) + 1
        if data.get("version") != versions[key]:
            problems.append(f"键 {key} 的版本不连续：第 {versions[key]} 次写入记为版本 "
                            f"{data.get('version')!r}，应为 {versions[key]}")
    caused = {cause for event in events if event.get("type") == "artifact.written"
              for cause in event.get("caused_by") or ()}
    for event in events:
        if event.get("type") != "model.called":
            continue
        data = event.get("data") or {}
        if data.get("output") is not None and event.get("seq") not in caused:
            problems.append(f"序号 {event.get('seq')}（{event.get('actor')}）的模型回复"
                            "没有以其为因的 artifact.written")
    usage_events = [event for event in events if event.get("type") == "artifact.written"
                    and (event.get("data") or {}).get("key") == "run/usage"]
    if usage_events:
        want = _usage_sums(events)
        got = (usage_events[-1].get("data") or {}).get("value")
        if want != got:
            problems.append("run/usage 与 model.called 的合计不一致")
    has_outcome = any(event.get("type") == "artifact.written"
                      and (event.get("data") or {}).get("key") == "run/outcome"
                      for event in events)
    if has_outcome != bool(finished):
        problems.append(f"run/outcome {'在而 run.finished 不在' if has_outcome else '不在而 run.finished 在'}")
    return problems


def _usage_sums(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 model.called 逐条合计用量，行的形状与预算表相同（role、model、calls、requests、
    input_tokens、output_tokens、cost；未定价的行为 None）。calls 数事件，其余三项来自
    每条的 usage，cost 只把有价的加起来。"""
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        if event.get("type") != "model.called":
            continue
        data = event.get("data") or {}
        usage = data.get("usage") or {}
        key = (str(event.get("actor", "")), str(data.get("model", "")))
        row = rows.setdefault(key, {"role": key[0], "model": key[1], "calls": 0,
                                    "requests": 0, "input_tokens": 0, "output_tokens": 0,
                                    "cost": None})
        row["calls"] += 1
        for name in ("requests", "input_tokens", "output_tokens"):
            row[name] += usage.get(name) or 0
        if data.get("cost") is not None:
            row["cost"] = (row["cost"] or 0.0) + data["cost"]
    return [rows[key] for key in sorted(rows)]
