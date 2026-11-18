"""指标字典：审计者的外部知识来源（L3 层独立性的物质基础）。

没有它，Critic 最多只能查"你算错了"，永远查不出"你算的定义不对"。
而口径错恰恰是数据分析里最常见、也最贵的错误。

设计要点：
    required_filters 用**布尔开关**表达，而不是自然语言描述。
    这样口径校验是确定性判断（集合比较），可测试、可复现——
    不需要再问一次模型"你觉得这个口径对吗"（那会引入新的不确定性）。
"""
from __future__ import annotations

import json
import os

from config import DATA_DIR

DEFAULT_PATH = os.path.join(DATA_DIR, "metric_dict.json")


class MetricDict:
    def __init__(self, path: str | None = None):
        self.path = path or DEFAULT_PATH
        with open(self.path, encoding="utf-8") as f:
            self.doc = json.load(f)
        self.entries = self.doc["metrics"]

    def get(self, name: str) -> dict | None:
        for e in self.entries:
            if e["name"] == name:
                return e
        return None

    def match(self, text: str) -> list[dict]:
        """按别名做最长匹配，避免"营收"命中"人均营收"这类误判。"""
        hits = []
        for e in self.entries:
            alias = max((a for a in e["aliases"] if a in text), key=len, default=None)
            if alias:
                hits.append((len(alias), e))
        hits.sort(key=lambda x: -x[0])
        return [e for _, e in hits]

    def required_issues(self, entry: dict, filters: dict) -> list[dict]:
        """返回未满足的口径要求。空列表 = 口径合规。"""
        issues = []
        for rf in entry.get("required_filters", []):
            if not filters.get(rf["key"]):
                issues.append(rf)
        return issues
