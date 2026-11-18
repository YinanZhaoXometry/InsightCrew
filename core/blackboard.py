"""黑板：所有智能体共享的唯一真相来源（single source of truth）。

为什么必须版本化 artifact：
    审计者的批注要能指向"具体哪一次执行"（a2），否则重跑之后
    没人说得清被驳回的到底是哪一版结果。答辩问"如何复盘"时，这就是答案。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict


@dataclass
class Step:
    step_id: int
    desc: str
    expected: str = ""
    status: str = "pending"


@dataclass
class Artifact:
    aid: str                 # a1, a2, ... 版本化
    step_id: int
    code: str
    ok: bool
    stdout: str = ""
    stderr: str = ""
    value: float | None = None
    unit: str = ""
    method: str = ""


@dataclass
class Critique:
    verdict: str                    # PASS / REVISE / UNVERIFIED
    issues: list = field(default_factory=list)   # [{layer, issue, detail}]
    evidence: dict = field(default_factory=dict)

    @property
    def layer(self) -> str:
        layers = sorted({i.get("layer", "?") for i in self.issues})
        return "+".join(layers) if layers else "L2+L3"

    def signature(self) -> str:
        """用于识别"同一个问题被反复驳回"（触发停滞终止）。"""
        return "|".join(sorted(i.get("issue", "") for i in self.issues))


@dataclass
class Blackboard:
    question: str
    profile: dict = field(default_factory=dict)
    plan: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    critiques: list = field(default_factory=list)
    filters: dict = field(default_factory=dict)     # 结构化口径参数，审计者直接读
    target_metric: str = ""
    report: str = ""
    turn: int = 0
    tokens: int = 0
    degraded: bool = False
    notes: list = field(default_factory=list)
    caveats: list = field(default_factory=list)     # 数据覆盖类的披露，必须进报告正文
    _aid: int = 0
    _plan_version: int = 0

    # ---- 写入 ----
    def add_artifact(self, step_id: int, code: str, run_result: dict,
                     value: float | None = None, unit: str = "", method: str = "") -> Artifact:
        self._aid += 1
        a = Artifact(aid=f"a{self._aid}", step_id=step_id, code=code,
                     ok=bool(run_result.get("ok")), stdout=run_result.get("stdout", ""),
                     stderr=run_result.get("stderr", ""), value=value, unit=unit, method=method)
        self.artifacts.append(a)
        return a

    def new_plan(self, steps: list[dict], filters: dict, target_metric: str):
        self._plan_version += 1
        self.plan = [Step(**s) for s in steps]
        self.filters = filters
        self.target_metric = target_metric
        return self._plan_version

    def note(self, text: str):
        self.notes.append(text)

    @property
    def latest_value(self) -> float | None:
        for a in reversed(self.artifacts):
            if a.value is not None:
                return a.value
        return None

    # ---- 输出 ----
    def to_dict(self) -> dict:
        return {
            "question": self.question, "profile": self.profile,
            "plan": [asdict(s) for s in self.plan],
            "artifacts": [asdict(a) for a in self.artifacts],
            "critiques": [asdict(c) for c in self.critiques],
            "filters": self.filters, "target_metric": self.target_metric,
            "report": self.report, "turn": self.turn, "tokens": self.tokens,
            "degraded": self.degraded, "notes": self.notes,
        }

    def save(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
