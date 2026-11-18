"""从自然语言问题里解析出分析参数（地区 / 时间范围 / 指标），并判断这个问题能不能答。

为什么需要它：
    离线演示模式没有模型可用，但用户会随便问问题。
    如果不解析，系统对任何问题都只会用"华东 Q3"这一套参数，
    演示时换个问题就答非所问，看起来像坏了。

    有了它，离线模式也能对**任意地区/时间段**的问题给出正确数字，
    而"算错→被驳回→重算"的剧情依然每道题都会发生。

它同时承担一件更重要的事：**范围检查（scope）**。
    解析不出来、或者算不了的问题，必须明确说"答不了"，
    而不是套一套默认参数、给出一个看起来很确定的错数字。
    静默答错比拒绝作答危险得多——这是本文件里最值得讲的设计取舍。
"""
from __future__ import annotations

import calendar
import csv
import os
import re
from datetime import date

REGIONS = ["华东", "华北", "华南", "华中", "西南", "西北", "东北"]
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}

METRIC_ALIASES = {
    "营收": ["营业收入", "净营收", "销售额", "营收", "收入", "revenue"],
    "订单量": ["订单量", "订单数", "单量", "order count"],
    "客单价": ["客单价", "平均订单金额", "average order value", "aov"],
    "退款率": ["退款率", "退货率", "refund rate"],
    "毛利率": ["毛利率", "gross margin"],
}
# 已实现"模型写代码 + SQL 独立复算"双路径的指标。
# 每加一个指标，必须同时在 tools/metrics.py 加实现、在 data/metric_dict.json 加口径，
# 否则 tools/metrics.consistency_report() 会报漂移。
SUPPORTED_METRICS = {"营收", "订单量", "客单价", "退款率"}

# 字典里有定义、但数据里缺字段，只能拒绝、不能估算的指标。
NO_DATA_METRICS = {"毛利率": "数据集没有成本（cost）字段，毛利率无法计算，不允许估算"}

# 字典里没有、数据集里也没有字段的指标。
# 不识别它们的话，问题会被默认当成"营收"，然后给出一个很像正确答案的错数字——
# 这是全系统最危险的一种失败，必须在解析层拦掉。
UNSUPPORTED_HINTS = {
    "营销费用": "营销费用", "推广费用": "推广费用", "费用": "费用类指标",
    "成本": "成本类指标", "毛利": "毛利率", "利润": "利润类指标",
    "库存": "库存类指标", "人数": "人数类指标", "转化率": "转化率",
    "留存": "留存率", "复购": "复购率", "roi": "ROI", "投放": "投放类指标",
    "预算": "预算类指标", "退款金额": "退款金额",
}

_COVERAGE: tuple[str, str] | None = None
_AVAILABLE_REGIONS: list[str] | None = None

# "看起来像数据分析问题"的最低门槛。
# 没有这些词的问题（"帮我写一首诗"）会被误解析成"营收·全年"，
# 然后给出一个格式完美、置信度很高的假答案——必须在解析层拦掉。
DOMAIN_WORDS = ("营收", "收入", "销售", "营业", "订单", "单量", "客单", "退款",
                "退货", "业绩", "gmv", "金额", "多少", "多少笔", "占比", "同比",
                "环比", "分析", "统计", "汇总", "数据")

# 数据里有、但系统还没实现下钻的维度。问到就要明确拒绝，不能静默忽略。
CHANNEL_WORDS = ("线上", "门店", "分销", "渠道", "线下")


# --------------------------------------------------------------------- 基础工具
def _num(tok: str) -> int:
    tok = (tok or "").strip()
    if tok.isdigit():
        return int(tok)
    return CN_NUM.get(tok, 0)


def _add_months(d: date, k: int) -> date:
    m = d.month - 1 + k
    y = d.year + m // 12
    return date(y, m % 12 + 1, 1)


def _month_range(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def _quarter_of(d: date) -> int:
    return (d.month - 1) // 3 + 1


def _quarter_range(y: int, n: int) -> tuple[date, date]:
    return _month_range(y, (n - 1) * 3 + 1)[0], _month_range(y, n * 3)[1]


def _regions(q: str) -> list[str]:
    """按出现顺序收集地区，支持"华南和华北"这类多地区提问。"""
    hits = [(q.index(r), r) for r in REGIONS if r in q]
    return [r for _, r in sorted(hits)]


def coverage() -> tuple[str, str]:
    """数据实际覆盖的日期区间。用来判断"这个问题是不是问到了空白处"。"""
    global _COVERAGE
    if _COVERAGE is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "orders.csv")
        lo, hi = "", ""
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                dates = [r["order_date"] for r in csv.DictReader(f) if r.get("order_date")]
            if dates:
                lo, hi = min(dates), max(dates)
        _COVERAGE = (lo, hi)
    return _COVERAGE


def available_regions() -> list[str]:
    """数据里**真实出现**的地区取值。

    为什么要单独查一遍：REGIONS 是"中国大区"的常识列表（七个区），
    而这份数据集只有三个区。如果拿常识列表去做校验，
    "西南 8 月营收"就会通过校验、查不到任何行、返回 0.00 元，
    并且因为"0"是一个合法数字，L2 复算与 L3 口径检查都会通过——
    系统会带着"置信度：高"交付一个假答案。
    校验必须以数据为准，不能以常识为准。
    """
    global _AVAILABLE_REGIONS
    if _AVAILABLE_REGIONS is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "orders.csv")
        vals: list[str] = []
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    v = (r.get("region") or "").strip()
                    if v and v not in vals:
                        vals.append(v)
        _AVAILABLE_REGIONS = sorted(vals)
    return _AVAILABLE_REGIONS


def has_metric_signal(question: str) -> bool:
    """问题里有没有任何"指标/数据"类的词。"""
    q = (question or "").lower()
    if any(a in q for aliases in METRIC_ALIASES.values() for a in aliases):
        return True
    if any(w in q for w, _ in UNSUPPORTED_HINTS.items()):
        return True
    return any(w in q for w in DOMAIN_WORDS)


# --------------------------------------------------------------------- 指标识别
def detect_metric(question: str) -> str:
    """返回问题在问哪个指标。

    优先匹配指标字典里的别名（最长匹配，避免"营收"命中"人均营收"）；
    一个都不命中时，再看是不是"字典里没有、数据里也没字段"的指标；
    仍没有就默认「营收」（例如"华东区卖了多少"）。
    """
    q = (question or "").lower()
    best, best_len = "", 0
    for name, aliases in METRIC_ALIASES.items():
        for a in aliases:
            if a in q and len(a) > best_len:
                best, best_len = name, len(a)
    if best:
        return best

    hint, hint_len = "", 0
    for word, label in UNSUPPORTED_HINTS.items():
        if word in q and len(word) > hint_len:
            hint, hint_len = label, len(word)
    return hint or "营收"


# --------------------------------------------------------------------- 主解析
def parse(question: str) -> dict:
    """返回 {region, start, end, span, metric}。

    region: str（单地区）| list[str]（多地区）| None（全部区域）
    """
    q = question or ""
    ref = date.today()
    metric = detect_metric(q)

    m = re.search(r"(20\d{2})\s*年", q)
    if m:
        year = int(m.group(1))
    elif "去年" in q:
        year = ref.year - 1
    elif re.search(r"今年|本年", q):
        year = ref.year
    else:
        year = None

    regions = _regions(q)
    region = None if not regions else (regions[0] if len(regions) == 1 else regions)

    def out(s: date, e: date, span: str) -> dict:
        return {"region": region, "start": s.isoformat(), "end": e.isoformat(),
                "span": span, "metric": metric}

    # 1) 滚动窗口：近 N 个月 / 最近 3 个月
    roll = re.search(r"(?:近|最近|过去)\s*([一二三四五六七八九十]{1,2}|\d{1,2})\s*个?\s*月", q)
    if roll:
        n = _num(roll.group(1))
        if 1 <= n <= 24:
            start = _add_months(date(ref.year, ref.month, 1), -(n - 1))
            return out(start, ref, f"近 {n} 个月")

    # 2) 相对季度：上季度 / 本季度
    if re.search(r"上(?:个)?季度|上一季度", q):
        n, y = _quarter_of(ref) - 1, ref.year
        if n == 0:
            n, y = 4, y - 1
        s, e = _quarter_range(y, n)
        return out(s, e, f"上季度（Q{n}）")
    if re.search(r"本季度|这季度|当季", q):
        n = _quarter_of(ref)
        s, e = _quarter_range(ref.year, n)
        return out(s, e, f"本季度（Q{n}）")

    # 3) 显式季度：第三季度 / 三季度 / Q3
    qm = re.search(r"第?\s*([一二三四1-4])\s*季度|q\s*([1-4])", q, re.I)
    if qm:
        n = _num(qm.group(1) or qm.group(2))
        y = year or ref.year
        if 1 <= n <= 4:
            s, e = _quarter_range(y, n)
            return out(s, e, f"Q{n}")

    # 4) 相对月份：上个月 / 本月
    if re.search(r"上(?:个)?月", q):
        d = _add_months(date(ref.year, ref.month, 1), -1)
        s, e = _month_range(d.year, d.month)
        return out(s, e, "上个月")
    if re.search(r"本月|这个月|当月", q):
        s, e = _month_range(ref.year, ref.month)
        return out(s, e, "本月")

    # 5) 显式月份：8 月 / 8月份 / 八月 / 十二月份
    mm = re.search(r"([一二三四五六七八九十]{1,2}|\d{1,2})\s*月份?", q)
    if mm:
        n = _num(mm.group(1))
        if 1 <= n <= 12:
            y = year or ref.year
            s, e = _month_range(y, n)
            return out(s, e, f"{n} 月")

    # 6) 全年
    y = year or ref.year
    return out(date(y, 1, 1), date(y, 12, 31), f"{y} 全年")


def scope(question: str) -> dict:
    """判断这个问题在当前版本里能不能给出可信答案。

    返回 {"ok": bool, "reason": str, "parsed": dict}
    同一条原则：**能答才答，答不了要明说。**
    """
    p = parse(question)
    metric = p["metric"]

    if not has_metric_signal(question):
        return {"ok": False, "parsed": p,
                "reason": ("这句话里没有任何指标或数据类词汇，判断不出要算什么。"
                           f"本系统只会回答指标字典里的问题（已实现：{'、'.join(sorted(SUPPORTED_METRICS))}）")}

    if metric not in SUPPORTED_METRICS:
        if metric in NO_DATA_METRICS:
            reason = f"「{metric}」：{NO_DATA_METRICS[metric]}"
        elif metric in METRIC_ALIASES:
            reason = (f"指标「{metric}」已收入口径字典，但当前版本尚未实现它的"
                      f"计算路径与独立复算路径（已实现：{'、'.join(sorted(SUPPORTED_METRICS))}）")
        else:
            reason = (f"「{metric}」不在指标字典覆盖范围内，数据集中也没有对应字段"
                      f"（口径字典只收录营收/订单量/客单价/退款率/毛利率）")
        return {"ok": False, "parsed": p, "reason": reason}

    # 渠道维度：数据里有 channel 字段，但当前版本的筛选/复算路径都没有实现它。
    # 不拦的话，"8 月华东线上渠道营收"会静默返回全渠道的数字——
    # 数字本身没错，回答的问题却错了。这类"维度被悄悄丢掉"最不容易被发现。
    hit = [w for w in CHANNEL_WORDS if w in question]
    if hit:
        return {"ok": False, "parsed": p,
                "reason": (f"当前版本不支持按渠道（{'、'.join(hit)}）下钻："
                           f"数据里有 channel 字段，但筛选与独立复算两条路径都只实现了"
                           f"「地区 × 时间」两个维度。若强行回答，会返回全渠道的数字，"
                           f"那是在回答另一个问题")}

    lo, hi = coverage()
    if lo and hi and (p["end"] < lo or p["start"] > hi):
        return {"ok": False, "parsed": p,
                "reason": f"数据覆盖 {lo} ~ {hi}，不含 {p['start']} ~ {p['end']}"}

    # 地区必须真实存在于数据里，而不是"中国版图上应该有"
    have = available_regions()
    asked = p["region"] if isinstance(p["region"], list) else ([p["region"]] if p["region"] else [])
    missing = [r for r in asked if have and r not in have]
    if missing:
        return {"ok": False, "parsed": p,
                "reason": (f"数据中只有 {'、'.join(have)} 三个地区的订单，"
                           f"没有 {'、'.join(missing)} 的记录。"
                           f"该问题查不到任何订单，不能回答 0 元——0 元和「没数据」不是一回事")}

    return {"ok": True, "parsed": p, "reason": ""}


def describe(p: dict) -> str:
    r = p["region"]
    if isinstance(r, list):
        where = "、".join(r)
    else:
        where = r or "全部区域"
    return f"{where} / {p['span']}（{p['start']} ~ {p['end']}）"
