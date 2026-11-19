# Adapted from TradingAgents (Apache-2.0), modified.
"""决策日志：一次已交付的运行记一条 decision，结算后补一条 outcome（JSONL）。

改写自 TradingAgents memory/log.py 与 dataflows/files.py，重写为本项目的契约：
每次写入都在跨进程文件锁（POSIX 用 fcntl.flock，Windows 用 msvcrt.locking，
按平台在函数内引入）内重写整个文件——先写旁边一个带随机后缀的临时文件，再
os.replace 换入；读者要么看到整份旧文件，要么看到整份新文件，绝无半截写入。

幂等与淘汰（BLUEPRINT 19.13）：同一 (as_of, ticker) 的第二条 decision 被忽略；
resolve 只给还挂着 pending 的决策补结果，同一决策的第二个 outcome 同样被忽略；
max_entries（0 = 不限）只淘汰最旧的“已结算对”（decision 与其 outcome 一起删），
pending 的决策永不被删。文件不存在时读为空；坏行拒绝而不是跳过（报错指出行号）。
"""
from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from ..domain.memory import DecisionRecord, OutcomeRecord

Record = DecisionRecord | OutcomeRecord


class DecisionLog:
    """决策日志的文件实现：一行一条记录，decision 与 outcome 混排在同一文件里。"""

    def __init__(self, path: str | Path, *, max_entries: int = 0):
        self._path = Path(path)
        self._max = max_entries

    def append(self, record: DecisionRecord) -> bool:
        """记一条决策。同一 (as_of, ticker) 已有 decision 时忽略，返回 False。"""
        with _locked(self._path):
            records = self._read()
            if any(isinstance(r, DecisionRecord)
                   and (r.as_of, r.ticker) == (record.as_of, record.ticker)
                   for r in records):
                return False
            records.append(record)
            self._write(self._rotate(records))
            return True

    def resolve(self, outcome: OutcomeRecord) -> bool:
        """给一条 pending 的决策补结果。没有对应决策、或它已有结果时忽略，返回 False。"""
        key = (outcome.as_of, outcome.ticker)
        with _locked(self._path):
            records = self._read()
            pending = any(isinstance(r, DecisionRecord) and (r.as_of, r.ticker) == key
                          for r in records)
            settled = any(isinstance(r, OutcomeRecord) and (r.as_of, r.ticker) == key
                          for r in records)
            if not pending or settled:
                return False
            records.append(outcome)
            self._write(self._rotate(records))
            return True

    def records(self) -> list[Record]:
        """全部记录，按写入顺序。无锁读：replace 是原子的，读到的一定是某一整份。"""
        return self._read()

    def pending(self) -> list[DecisionRecord]:
        """还没有结果的决策，按写入顺序。"""
        records = self._read()
        settled = {(r.as_of, r.ticker) for r in records if isinstance(r, OutcomeRecord)}
        return [r for r in records
                if isinstance(r, DecisionRecord) and (r.as_of, r.ticker) not in settled]

    def _rotate(self, records: list[Record]) -> list[Record]:
        """已结算对超过 max_entries 时，从最旧的起整对删除（按 as_of，再按写入顺序）。"""
        if self._max <= 0:
            return records
        settled_at = {(r.as_of, r.ticker): i for i, r in enumerate(records)
                      if isinstance(r, OutcomeRecord)}
        pairs = sorted((records[i].as_of, records[i].ticker, i)
                       for i, r in enumerate(records)
                       if isinstance(r, DecisionRecord)
                       and (r.as_of, r.ticker) in settled_at)
        excess = len(pairs) - self._max
        if excess <= 0:
            return records
        drop = {(as_of, ticker) for as_of, ticker, _ in pairs[:excess]}
        return [r for r in records if (r.as_of, r.ticker) not in drop]

    def _read(self) -> list[Record]:
        if not self._path.exists():
            return []
        out: list[Record] = []
        for lineno, line in enumerate(
                self._path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{self._path} 第 {lineno} 行不是 JSON：{e}") from e
            if not isinstance(obj, dict) or obj.get("kind") not in ("decision", "outcome"):
                raise ValueError(f"{self._path} 第 {lineno} 行不是决策或结果记录")
            try:
                out.append((OutcomeRecord if obj["kind"] == "outcome"
                            else DecisionRecord).model_validate(obj))
            except ValidationError as e:
                raise ValueError(f"{self._path} 第 {lineno} 行不合契约：{e}") from e
        return out

    def _write(self, records: list[Record]) -> None:
        """整份重写：锁内、临时文件、os.replace。临时文件名带随机后缀，两个进程不共用。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_name(f"{self._path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text("".join(r.model_dump_json() + "\n" for r in records),
                            encoding="utf-8")
            os.replace(temp, self._path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """跨进程持锁：锁在旁边的 <path>.lock 上，不在数据文件上（replace 会换掉它的 inode）。"""
    # 按平台选用锁实现；import 放在函数内，另一平台的模块不会被引入。
    with open(f"{path}.lock", "a+b") as handle, \
            (_hold_windows(handle) if os.name == "nt" else _hold_posix(handle)):
        yield


@contextmanager
def _hold_posix(handle) -> Iterator[None]:
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _hold_windows(handle) -> Iterator[None]:
    import errno
    import msvcrt
    from typing import Any, cast

    # typeshed 只在 Windows 平台暴露 msvcrt 的这几个名字；cast 成 Any 让其他平台的
    # 类型检查通过。这段代码本身只在 os.name == "nt" 时才会执行。
    win = cast(Any, msvcrt)
    handle.seek(0)
    while True:
        try:
            win.locking(handle.fileno(), win.LK_LOCK, 1)
            break
        except OSError as e:
            # LK_LOCK 等约十秒后放弃（EDEADLK）：那不是等待，重试；其余照实抛出。
            if e.errno != errno.EDEADLK:
                raise
    try:
        yield
    finally:
        handle.seek(0)
        win.locking(handle.fileno(), win.LK_UNLCK, 1)
